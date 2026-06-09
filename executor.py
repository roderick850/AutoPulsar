     1|import subprocess
     2|import threading
     3|import time
     4|import os
     5|
     6|try:
     7|    from icon_detector import check_conditions, check_icon, check_icon_multi
     8|    HAS_ICON_DETECTOR = True
     9|except ImportError:
    10|    HAS_ICON_DETECTOR = False
    11|
    12|try:
    13|    from macro_player import MacroPlayer, actions_to_events
    14|    HAS_MACRO = True
    15|except ImportError:
    16|    HAS_MACRO = False
    17|
    18|
    19|def _normalize_conditions(item):
    20|    """Convierte formato antiguo (repeat_until_*) a formato unificado.
    21|
    22|    Retorna (conditions_dict, is_repeat_until).
    23|    conditions_dict siempre tiene la forma unificada:
    24|    {
    25|        "action": "require" | "repeat_until",
    26|        "mode": "and" | "or",
    27|        "items": [...],
    28|        "repeat": {...},   # solo si action == "repeat_until"
    29|        "retry": {...},
    30|        "fallback": {...},
    31|    }
    32|    """
    33|    conditions = item.get("conditions", {}) or {}
    34|
    35|    # ── Migrar formato antiguo repeat_until_* ──
    36|    if item.get("repeat_until_enabled") and item.get("repeat_until_icon"):
    37|        icon = item["repeat_until_icon"]
    38|        ru_mode = item.get("repeat_until_mode", "match")
    39|        # Crear condición unificada (siempre "require" — stop_when
    40|        # maneja la diferencia entre match y no_match)
    41|        cond_item = {
    42|            "type": "require",
    43|            "icon_path": icon,
    44|            "label": os.path.basename(icon),
    45|            "threshold": item.get("repeat_until_threshold", 0.08),
    46|        }
    47|        conditions = {
    48|            "action": "repeat_until",
    49|            "mode": "and",
    50|            "items": [cond_item],
    51|            "repeat": {
    52|                "stop_when": ru_mode,
    53|                "max_iterations": item.get("repeat_until_max_iterations", 0),
    54|                "check_interval": item.get("repeat_until_check_interval", 0.5),
    55|            },
    56|            "retry": {"enabled": False, "count": 3, "delay": 5},
    57|            "fallback": {"enabled": False, "threshold": 3,
    58|                         "script": "", "delay_after": 0},
    59|        }
    60|        return conditions, True
    61|
    62|    # ── Si ya tiene el formato unificado ──
    63|    if conditions.get("action"):
    64|        return conditions, (conditions.get("action") == "repeat_until")
    65|
    66|    # ── Condiciones "legacy" sin action → asumir "require" ──
    67|    if conditions.get("items"):
    68|        conditions["action"] = "require"
    69|        return conditions, False
    70|
    71|    # Sin condiciones
    72|    return conditions, False
    73|
    74|
    75|def _check_icon_with_threshold(cond):
    76|    """Verifica un icono usando el threshold, región y multi-muestreo
    77|    definidos en la condición."""
    78|    threshold = cond.get("threshold", 0.08)
    79|    region = cond.get("region")  # [x, y, w, h] o None
    80|    samples = cond.get("samples", 1)
    81|    confidence = cond.get("confidence", 1.0)
    82|    return check_icon_multi(
    83|        cond.get("icon_path", ""), region, threshold,
    84|        samples=samples, confidence=confidence)
    85|
    86|
    87|def _evaluate_items(items, mode):
    88|    """Evalúa una lista de condiciones (require/block) con modo AND/OR.
    89|
    90|    Retorna (passed, reasons).
    91|    """
    92|    if not items:
    93|        return True, None
    94|
    95|    results = []
    96|    reasons = []
    97|
    98|    for cond in items:
    99|        icon_path = cond.get("icon_path", "")
   100|        ctype = cond.get("type", "require")
   101|
   102|        if not icon_path:
   103|            passed = True if ctype == "block" else False
   104|            results.append(passed)
   105|            if not passed:
   106|                reasons.append("Requiere icono (sin ruta)")
   107|            continue
   108|
   109|        found, error = _check_icon_with_threshold(cond)
   110|
   111|        if ctype == "require":
   112|            results.append(found)
   113|            if not found:
   114|                tag = "falta icono" if error == "missing" else "icono no visible"
   115|                reasons.append(
   116|                    f"Requerir: {tag} ({os.path.basename(icon_path)})")
   117|        elif ctype == "block":
   118|            blocked = not found
   119|            results.append(blocked)
   120|            if not blocked:
   121|                reasons.append(
   122|                    f"Bloquear: icono visible "
   123|                    f"({os.path.basename(icon_path)})")
   124|        else:
   125|            results.append(found)
   126|
   127|    if mode == "or":
   128|        passed = any(results)
   129|    else:
   130|        passed = all(results)
   131|
   132|    if passed:
   133|        return True, None
   134|    return False, "; ".join(reasons) if reasons else "condiciones no cumplidas"
   135|
   136|
   137|class Executor(threading.Thread):
   138|    def __init__(self, playlist, settings, callbacks,
   139|                 stop_event, launch_event):
   140|        super().__init__(daemon=True)
   141|        self.playlist = playlist
   142|        self.settings = settings
   143|        self.callbacks = callbacks
   144|        self.stop_event = stop_event
   145|        self.launch_event = launch_event
   146|
   147|    def run(self):
   148|        if len(self.playlist) == 0:
   149|            self._safe_callback("on_finish", "Lista vacía", 0, 0, 0, 0)
   150|            return
   151|
   152|        loop_mode = self.settings.get("loop_mode", "once")
   153|        loop_count = self.settings.get("loop_count", 1)
   154|        loop_delay = self.settings.get("loop_delay", 0)
   155|
   156|        if loop_mode == "infinite":
   157|            max_loops = None
   158|        elif loop_mode == "fixed":
   159|            max_loops = loop_count
   160|        else:
   161|            max_loops = 1
   162|
   163|        total_reps_per_loop = sum(
   164|            item["repetitions"] for item in self.playlist)
   165|
   166|        once_reps = sum(item["repetitions"] for item in self.playlist
   167|                        if item.get("first_loop_only", False))
   168|        repeat_reps = total_reps_per_loop - once_reps
   169|
   170|        if max_loops is None:
   171|            total_global_reps = None
   172|        else:
   173|            total_global_reps = once_reps + repeat_reps * max_loops
   174|
   175|        current_loop = 0
   176|        completed_reps_total = 0
   177|
   178|        time.sleep(1)
   179|
   180|        self._safe_callback("on_start_run", total_global_reps,
   181|                            total_reps_per_loop, max_loops)
   182|
   183|        while True:
   184|            if self.stop_event.is_set():
   185|                break
   186|
   187|            if max_loops is not None and current_loop >= max_loops:
   188|                break
   189|
   190|            current_loop += 1
   191|
   192|            self._safe_callback("on_start_loop", current_loop,
   193|                                max_loops, total_global_reps)
   194|
   195|            for idx, item in enumerate(self.playlist):
   196|                if self.stop_event.is_set():
   197|                    break
   198|
   199|                if current_loop > 1 and item.get("first_loop_only", False):
   200|                    continue
   201|
   202|                name = os.path.basename(item["path"])
   203|                reps = item["repetitions"]
   204|                duration = item["duration"]
   205|                pause = item["pause"]
   206|
   207|                # ── Normalizar condiciones (formato unificado) ──
   208|                conditions, is_repeat_until = _normalize_conditions(item)
   209|                has_conditions = bool(
   210|                    conditions.get("items") and HAS_ICON_DETECTOR)
   211|
   212|                if is_repeat_until:
   213|                    mode_label = "(hasta condición)"
   214|                else:
   215|                    mode_label = ""
   216|
   217|                self._safe_callback("on_start_item", idx, name,
   218|                    None if is_repeat_until else reps,
   219|                    mode_label)
   220|
   221|                if is_repeat_until and HAS_ICON_DETECTOR:
   222|                    # ── REPEAT UNTIL (unificado) ──
   223|                    repeat_cfg = conditions.get("repeat", {})
   224|                    stop_when = repeat_cfg.get("stop_when", "match")
   225|                    ru_max = repeat_cfg.get("max_iterations", 0)
   226|                    ru_interval = repeat_cfg.get("check_interval", 0.5)
   227|                    ru_unlimited = (ru_max == 0)
   228|
   229|                    # ── Verificar ANTES de la primera ejecución ──
   230|                    ok, reason = _evaluate_items(
   231|                        conditions.get("items", []),
   232|                        conditions.get("mode", "and"))
   233|                    if stop_when == "match":
   234|                        condition_already_met = ok
   235|                    else:  # no_match
   236|                        condition_already_met = not ok
   237|
   238|                    if condition_already_met:
   239|                        # La condición ya se cumple → detener todo
   240|                        self._safe_callback(
   241|                            "on_repeat_until_done",
   242|                            idx, name, 0)
   243|                        self.stop_event.set()
   244|                        break
   245|
   246|                    iteration = 0
   247|                    while ((ru_unlimited or iteration < ru_max)
   248|                           and not self.stop_event.is_set()):
   249|                        iteration += 1
   250|
   251|                        # ── Intervalo entre iteraciones ──
   252|                        if ru_interval > 0 and iteration > 1:
   253|                            slept = 0.0
   254|                            while (slept < ru_interval
   255|                                   and not self.stop_event.is_set()):
   256|                                time.sleep(0.1)
   257|                                slept += 0.1
   258|
   259|                        if self.stop_event.is_set():
   260|                            break
   261|
   262|                        self._safe_callback(
   263|                            "on_repeat",
   264|                            completed_reps_total + 1,
   265|                            total_global_reps,
   266|                            total_reps_per_loop,
   267|                            name,
   268|                            iteration,
   269|                            ru_max,
   270|                            current_loop,
   271|                            max_loops,
   272|                        )
   273|
   274|                        # ── Lanzar script ──
   275|                        try:
   276|                            self.launch_event.clear()
   277|                            self._safe_callback(
   278|                                "on_launch", item["path"])
   279|                            if not self.launch_event.wait(timeout=10):
   280|                                raise TimeoutError(
   281|                                    "El hilo principal no pudo "
   282|                                    "lanzar el .exe")
   283|                            time.sleep(2.0)
   284|                        except Exception as e:
   285|                            self._safe_callback("on_error", str(e))
   286|                            break
   287|
   288|                        completed_reps_total += 1
   289|
   290|                        # ── Esperar duración completa ──
   291|                        slept = 0.0
   292|                        while (slept < duration
   293|                               and not self.stop_event.is_set()):
   294|                            time.sleep(0.1)
   295|                            slept += 0.1
   296|
   297|                        if self.stop_event.is_set():
   298|                            break
   299|
   300|                        # ── Verificar condición al final ──
   301|                        ok2, _ = _evaluate_items(
   302|                            conditions.get("items", []),
   303|                            conditions.get("mode", "and"))
   304|                        if stop_when == "match":
   305|                            condition_met = ok2
   306|                        else:
   307|                            condition_met = not ok2
   308|
   309|                        self._safe_callback(
   310|                            "on_repeat_until_check", idx, name,
   311|                            iteration, ru_max,
   312|                            ok2, condition_met)
   313|
   314|                        if condition_met:
   315|                            self._safe_callback(
   316|                                "on_repeat_until_done",
   317|                                idx, name, iteration)
   318|                            self.stop_event.set()
   319|                            break
   320|
   321|                    if (not ru_unlimited and iteration >= ru_max
   322|                            and not self.stop_event.is_set()):
   323|                        self._safe_callback(
   324|                            "on_repeat_until_max",
   325|                            idx, name, ru_max)
   326|
   327|                else:
   328|                    # ── FIXED REPETITIONS ──
   329|                    for r in range(reps):
   330|                        if self.stop_event.is_set():
   331|                            break
   332|
   333|                        # ── Verificar condiciones ANTES ──
   334|                        if has_conditions:
   335|                            ok = self._check_conditions_with_retry(
   336|                                conditions, idx, item)
   337|                            if not ok:
   338|                                break  # saltar reps restantes
   339|
   340|                        self._safe_callback(
   341|                            "on_repeat",
   342|                            completed_reps_total + 1,
   343|                            total_global_reps,
   344|                            total_reps_per_loop,
   345|                            name,
   346|                            r + 1,
   347|                            reps,
   348|                            current_loop,
   349|                            max_loops,
   350|                        )
   351|
   352|                        try:
   353|                            self.launch_event.clear()
   354|                            self._safe_callback(
   355|                                "on_launch", item["path"])
   356|                            if not self.launch_event.wait(timeout=10):
   357|                                raise TimeoutError(
   358|                                    "El hilo principal no pudo "
   359|                                    "lanzar el .exe")
   360|                            time.sleep(2.0)
   361|                        except Exception as e:
   362|                            self._safe_callback("on_error", str(e))
   363|                            break
   364|
   365|                        completed_reps_total += 1
   366|
   367|                        slept = 0.0
   368|                        while (slept < duration
   369|                               and not self.stop_event.is_set()):
   370|                            time.sleep(0.1)
   371|                            slept += 0.1
   372|
   373|                        if self.stop_event.is_set():
   374|                            break
   375|
   376|                        if r < reps - 1 and pause > 0:
   377|                            slept = 0.0
   378|                            while (slept < pause
   379|                                   and not self.stop_event.is_set()):
   380|                                time.sleep(0.1)
   381|                                slept += 0.1
   382|
   383|                        if self.stop_event.is_set():
   384|                            break
   385|
   386|            # Delay entre loops
   387|            if ((max_loops is None or current_loop < max_loops)
   388|                    and loop_delay > 0):
   389|                self._safe_callback(
   390|                    "on_loop_delay", current_loop,
   391|                    loop_delay, total_global_reps)
   392|                slept = 0.0
   393|                while (slept < loop_delay
   394|                       and not self.stop_event.is_set()):
   395|                    time.sleep(0.1)
   396|                    slept += 0.1
   397|
   398|            if self.stop_event.is_set():
   399|                break
   400|
   401|        self._safe_callback(
   402|            "on_finish",
   403|            "Detenido" if self.stop_event.is_set()
   404|            else "Completado",
   405|            completed_reps_total,
   406|            total_global_reps,
   407|            total_reps_per_loop,
   408|            current_loop,
   409|            max_loops,
   410|        )
   411|
   412|    def _check_conditions_with_retry(self, conditions, idx, item):
   413|        """Verifica condiciones con reintentos + fallback.
   414|
   415|        Retorna True si las condiciones se cumplen (script debe ejecutarse).
   416|        Retorna False si fallan (saltar esta repetición/script).
   417|        Usa check_conditions() original de icon_detector (probado en batalla).
   418|        """
   419|        retry_cfg = conditions.get("retry", {})
   420|        retry_enabled = retry_cfg.get("enabled", False)
   421|        retry_count = retry_cfg.get("count", 1) if retry_enabled else 1
   422|        retry_delay = retry_cfg.get("delay", 3) if retry_enabled else 0
   423|
   424|        cond_met = False
   425|        for attempt in range(retry_count):
   426|            if self.stop_event.is_set():
   427|                return False
   428|            ok, reason = check_conditions(conditions)
   429|            if ok:
   430|                cond_met = True
   431|                break
   432|            if attempt < retry_count - 1 and retry_delay > 0:
   433|                self._safe_callback(
   434|                    "on_retry_wait", idx,
   435|                    os.path.basename(item["path"]),
   436|                    attempt + 1, retry_count)
   437|                slept = 0.0
   438|                while (slept < retry_delay
   439|                       and not self.stop_event.is_set()):
   440|                    time.sleep(0.1)
   441|                    slept += 0.1
   442|
   443|        if not cond_met:
   444|            item["_consecutive_failures"] = \
   445|                item.get("_consecutive_failures", 0) + 1
   446|
   447|            fallback_cfg = conditions.get("fallback", {})
   448|            if fallback_cfg.get("enabled", False):
   449|                threshold = fallback_cfg.get("threshold", 3)
   450|                fallback_script = fallback_cfg.get("script", "")
   451|                if (item["_consecutive_failures"] >= threshold
   452|                        and fallback_script):
   453|                    self._safe_callback(
   454|                        "on_fallback_trigger", idx,
   455|                        os.path.basename(item["path"]),
   456|                        os.path.basename(fallback_script))
   457|                    try:
   458|                        subprocess.Popen(fallback_script, shell=True)
   459|                    except Exception as e:
   460|                        self._safe_callback(
   461|                            "on_fallback_error",
   462|                            os.path.basename(fallback_script),
   463|                            str(e))
   464|                    item["_consecutive_failures"] = 0
   465|
   466|                    fb_delay = fallback_cfg.get("delay_after", 0)
   467|                    if fb_delay > 0:
   468|                        self._safe_callback(
   469|                            "on_fallback_wait", idx,
   470|                            os.path.basename(item["path"]),
   471|                            fb_delay)
   472|                        slept = 0.0
   473|                        while (slept < fb_delay
   474|                               and not self.stop_event.is_set()):
   475|                            time.sleep(0.1)
   476|                            slept += 0.1
   477|
   478|            self._safe_callback(
   479|                "on_skip_icon", idx,
   480|                os.path.basename(item["path"]))
   481|            return False
   482|
   483|        return True
   484|
   485|    def _play_macro(self, item):
        """Ejecuta una macro grabada."""
        macro_data = item.get("macro_data", {})
        actions = macro_data.get("actions", [])
        events = actions_to_events(actions)

        self._safe_callback("on_launch", f"Macro: {macro_data.get('name', 'sin nombre')}")
        player = MacroPlayer(events)
        player.play(block=True)

    def _safe_callback(self, name, *args):
   486|        cb = self.callbacks.get(name)
   487|        if cb:
   488|            try:
   489|                cb(*args)
   490|            except Exception as e:
   491|                print(f"Callback error {name}: {e}")
   492|