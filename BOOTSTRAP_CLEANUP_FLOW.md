# Bootstrap Cleanup Flow Diagram

## Időzítés és Folyamat (Timing and Process)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         SCRIPT INDÍTÁS (START)                          │
│                         RUN_STARTED_AT = time.time()                    │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    BOOTSTRAP FÁZIS (0-50 másodperc)                     │
├─────────────────────────────────────────────────────────────────────────┤
│  ✅ Login végrehajtása                                                  │
│  ✅ MAIN tab megnyitása (en.surebet.com/surebets)                      │
│  ✅ GROUP tabok nyitása (background worker)                            │
│  ✅ NEXT tabok nyitása (background worker)                             │
│  ✅ Tbody ID-k gyűjtése last_seen_ts-be                                │
│  ✅ Autoupdate bekapcsolása (Shift+P)                                  │
│                                                                         │
│  ❌ SAVE műveletek - LETILTVA                                          │
│  ❌ UPDATE műveletek - LETILTVA                                        │
│  ❌ DELETE műveletek - LETILTVA                                        │
│  ❌ NAV worker - NEM INDUL                                             │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 │ 50 másodperc eltelt
                                 │ in_bootstrap_phase() == False
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              🧹 POST-BOOTSTRAP CLEANUP (50-51 másodperc)                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  1️⃣  if not bootstrap_cleanup_done and not bootstrap:                 │
│      └─► Trigger: bootstrap fázis éppen véget ért                     │
│                                                                         │
│  2️⃣  full_resync_and_cleanup() meghívása                              │
│      └─► collect_live_ids_from_open_tabs()                            │
│           ├─► MAIN_HANDLE szkennelése                                 │
│           ├─► group_tabs szkennelése                                  │
│           └─► next_tabs szkennelése                                   │
│           ━━► live_ids set létrehozása                                │
│                                                                         │
│  3️⃣  Összehasonlítás                                                  │
│      active_ids (txt-ből betöltve) VS live_ids (tabok szkennelése)    │
│      └─► stale = [tid for tid in active_ids if tid not in live_ids]  │
│                                                                         │
│  4️⃣  Törlések ütemezése                                               │
│      for tid in stale:                                                 │
│          schedule_delete(tid)                                          │
│          └─► dispatcher.enqueue_delete(tid)                           │
│               └─► DELETE API hívás Supabase-be                        │
│                                                                         │
│  5️⃣  Eredmények feldolgozása                                          │
│      process_dispatcher_results()                                      │
│      └─► "delete_ok" ág:                                              │
│           ├─► active_ids.remove(tid)                                  │
│           ├─► save_active_all(active_ids) ← active_ids.txt UPDATE    │
│           ├─► seen.remove(tid)                                        │
│           └─► remove_seen_line(tid) ← seen_ids.txt UPDATE            │
│                                                                         │
│  6️⃣  bootstrap_cleanup_done = True                                    │
│      └─► Többet nem fut le                                            │
│                                                                         │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  NORMÁL MŰKÖDÉS (51+ másodperc)                         │
├─────────────────────────────────────────────────────────────────────────┤
│  ✅ NAV worker elindul                                                 │
│  ✅ SAVE műveletek engedélyezve                                        │
│  ✅ UPDATE műveletek engedélyezve                                      │
│  ✅ DELETE műveletek engedélyezve                                      │
│  ✅ Folyamatos ID figyelés és szinkronizáció                           │
│  ✅ Tab cleanup worker (250 mp-enként)                                 │
│  ✅ Account rotation (32 perc után)                                    │
└─────────────────────────────────────────────────────────────────────────┘
```

## Adatfolyam (Data Flow)

```
┌──────────────────┐
│  active_ids.txt  │ ◄─────────┐
│                  │            │
│  old_id_1        │            │ save_active_all()
│  old_id_2        │            │ amikor DELETE sikeres
│  old_id_3        │            │
│  live_id_1       │            │
│  live_id_2       │            │
└─────────┬────────┘            │
          │                     │
          │ load_active()       │
          │ (indításkor)        │
          ▼                     │
    ┌──────────┐                │
    │ active_   │                │
    │  ids     │                │
    │  (set)   │◄───────┐       │
    └────┬─────┘        │       │
         │              │       │
         │              │       │
         │              │       │
         └──────────────┼───────┼──────────────┐
                        │       │              │
                        │       │              │
    ┌───────────────────▼───────┴────┐         │
    │  collect_live_ids_from_tabs()  │         │
    │                                 │         │
    │  MAIN tab ──► tbody[data-id]   │         │
    │  GROUP tabs ─► tbody[data-id]  │         │
    │  NEXT tabs ──► tbody[data-id]  │         │
    └───────────────────┬─────────────┘         │
                        │                       │
                        ▼                       │
                  ┌──────────┐                  │
                  │ live_ids │                  │
                  │  (set)   │                  │
                  └────┬─────┘                  │
                       │                        │
                       │                        │
         ┌─────────────┴─────────────┐          │
         │ stale = active_ids -      │          │
         │         live_ids           │          │
         │                            │          │
         │ (IDs that are in           │          │
         │  active_ids.txt but NOT    │          │
         │  found in any open tabs)   │          │
         └──────────────┬─────────────┘          │
                        │                        │
                        │ for tid in stale       │
                        ▼                        │
              ┌──────────────────┐               │
              │ schedule_delete()│               │
              └─────────┬────────┘               │
                        │                        │
                        ▼                        │
            ┌────────────────────────┐           │
            │  dispatcher.enqueue_   │           │
            │      delete(tid)       │           │
            └───────────┬────────────┘           │
                        │                        │
                        ▼                        │
            ┌────────────────────────┐           │
            │   DELETE API hívás     │           │
            │   Supabase server      │           │
            └───────────┬────────────┘           │
                        │                        │
                        ▼                        │
            ┌────────────────────────┐           │
            │ process_dispatcher_    │           │
            │    results()           │           │
            │                        │           │
            │ "delete_ok" esetén:    │           │
            │  - active_ids.remove() ├───────────┘
            │  - save_active_all()   │
            └────────────────────────┘
```

## Példa Forgatókönyv (Example Scenario)

### Indítás előtt (Before Start)

**active_ids.txt tartalom:**
```
old_id_1
old_id_2
old_id_3
old_id_4
old_id_5
live_id_1
live_id_2
```

### Bootstrap fázis (0-50s)

Tabok nyílnak, szkennelődnek:
- MAIN tab: `live_id_1`, `live_id_2`, `live_id_6`
- GROUP tab 1: `live_id_3`
- GROUP tab 2: `live_id_4`
- NEXT tab: `live_id_5`

### Cleanup (50-51s)

1. **live_ids összegyűjtése:**
   ```python
   live_ids = {
       'live_id_1', 'live_id_2', 'live_id_3',
       'live_id_4', 'live_id_5', 'live_id_6'
   }
   ```

2. **active_ids betöltése:**
   ```python
   active_ids = {
       'old_id_1', 'old_id_2', 'old_id_3',
       'old_id_4', 'old_id_5', 'live_id_1', 'live_id_2'
   }
   ```

3. **Stale IDs meghatározása:**
   ```python
   stale = ['old_id_1', 'old_id_2', 'old_id_3', 'old_id_4', 'old_id_5']
   ```

4. **Törlések:**
   - DELETE hívások mindegyik old_id-ra
   - active_ids.txt frissül

### Cleanup után (After 51s)

**active_ids.txt új tartalom:**
```
live_id_1
live_id_2
live_id_3
live_id_4
live_id_5
live_id_6
```

**Konzol log:**
```
[11:45:25] 🧹 BOOTSTRAP fázis befejeződött – indítás utáni cleanup elindítva...
[11:45:25] 🔄 TAB-RESYNC indul (nyitott MAIN/GROUP/NEXT tabok alapján)…
[11:45:25] collect_live_ids_from_open_tabs: 6 élő tbody ID a nyitott tabokból
[11:45:25] 🗑️ TAB-RESYNC: 5 ID már nem él → törlés Supabase + txt
[11:45:26] ❌ DELETE kész: old_id_1 cid=abc-123
[11:45:26] ❌ DELETE kész: old_id_2 cid=def-456
[11:45:26] ❌ DELETE kész: old_id_3 cid=ghi-789
[11:45:26] ❌ DELETE kész: old_id_4 cid=jkl-012
[11:45:26] ❌ DELETE kész: old_id_5 cid=mno-345
[11:45:26] ✅ Bootstrap utáni cleanup befejezve
[11:45:26] 🔁 TAB-RESYNC kész.
```

---

Ez a diagram szemlélteti a teljes folyamatot az indulástól a tisztításon át a normál működésig.
