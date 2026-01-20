# Bootstrap Cleanup Implementation

## Összefoglalás (Summary)

A kód most automatikusan végrehajtja a tisztítást (cleanup) a 50 másodperces bootstrap fázis után.

The code now automatically executes cleanup after the 50-second bootstrap phase.

## A Probléma (The Problem)

A felhasználó azt kérte, hogy a 50 másodperces tab-nyitási fázis után:
1. Szkenneljük az összes megnyitott oldalt (main, group, next)
2. Gyűjtsük össze az élő tbody ID-kat
3. Hasonlítsuk össze az active_ids.txt fájlban lévő ID-kkal
4. Töröljük azokat az ID-kat, amelyek már nem élnek:
   - A szerverről (Supabase DELETE API)
   - Az active_ids.txt fájlból

The user requested that after the 50-second tab opening phase:
1. Scan all opened pages (main, group, next)
2. Collect live tbody IDs
3. Compare with IDs in active_ids.txt
4. Delete IDs that are no longer alive:
   - From the server (Supabase DELETE API)
   - From the active_ids.txt file

## A Megoldás (The Solution)

### 1. Bootstrap Fázis (Bootstrap Phase)

A kód már rendelkezett egy 50 másodperces bootstrap fázissal:
- `BOOTSTRAP_SEC = 50.0` - Változó a konfigban
- `in_bootstrap_phase()` - Függvény ellenőrzi, hogy még a bootstrap alatt vagyunk-e
- Ezen idő alatt CSAK tab-nyitás és ID-gyűjtés történik, NINCS SAVE/UPDATE/DELETE

The code already had a 50-second bootstrap phase:
- `BOOTSTRAP_SEC = 50.0` - Configuration variable
- `in_bootstrap_phase()` - Function checks if we're still in bootstrap
- During this time ONLY tab opening and ID collection happens, NO SAVE/UPDATE/DELETE

### 2. Meglévő Cleanup Funkció (Existing Cleanup Function)

A `full_resync_and_cleanup()` függvény már létezett és pontosan azt csinálja, amit kértél:

The `full_resync_and_cleanup()` function already existed and does exactly what was requested:

```python
def full_resync_and_cleanup(max_groups=None):
    """
    ÚJ: TAB-ALAPÚ RESYNC
    
    - NEM mászkál driver.get-tel oldalról oldalra
    - CSAK a már nyitott tabokat nézi végig (MAIN + GROUP + NEXT)
    - Ami active_ids-ben van, de sehol nem látszik → DELETE (Supabase + TXT)
    """
```

Ez a függvény:
1. Meghívja a `collect_live_ids_from_open_tabs()` függvényt
2. Összegyűjti az élő ID-kat a MAIN, GROUP és NEXT tabokról
3. Összehasonlítja az `active_ids` set-tel (mely az active_ids.txt fájlból töltődik be)
4. A nem létező ID-kat `schedule_delete()` hívással törli

This function:
1. Calls `collect_live_ids_from_open_tabs()` function
2. Collects live IDs from MAIN, GROUP and NEXT tabs
3. Compares with the `active_ids` set (loaded from active_ids.txt)
4. Deletes non-existent IDs using `schedule_delete()` call

### 3. Automatikus Trigger (Automatic Trigger)

A módosítás hozzáadta az automatikus triggerést:

The modification added automatic triggering:

```python
# Bootstrap cleanup flag - csak egyszer futtatjuk a bootstrap után
bootstrap_cleanup_done = False

# ... a fő loop-ban ...

# 🧹 BOOTSTRAP utáni tisztítás: csak egyszer, közvetlenül a bootstrap után
if not bootstrap_cleanup_done and not bootstrap:
    log("🧹 BOOTSTRAP fázis befejeződött – indítás utáni cleanup elindítva...")
    try:
        full_resync_and_cleanup()
        bootstrap_cleanup_done = True
        log("✅ Bootstrap utáni cleanup befejezve")
    except Exception as e:
        warn(f"⚠️ Bootstrap cleanup hiba: {e}")
        # Ha hiba van, ne próbáljuk újra
        bootstrap_cleanup_done = True
```

## Hogyan Működik (How It Works)

### Időzítés (Timing)

```
Időbeli folyamat:
0s ────── 50s ────── 51s ──────>
   BOOTSTRAP   CLEANUP   NORMÁL
   (csak       (egyszeri (folyamatos
   nyitás)     tisztítás) működés)
```

### Lépések (Steps)

1. **0-50 másodperc**: Bootstrap fázis
   - Main, Group, Next oldalak nyitása
   - Tbody ID-k gyűjtése
   - NINCS SAVE/UPDATE/DELETE művelet

2. **~50-51 másodperc**: Egyszeri cleanup
   - `full_resync_and_cleanup()` fut
   - Szkenneli az összes nyitott tabot
   - Élő ID-k összegyűjtése
   - active_ids.txt összehasonlítás
   - Nem élő ID-k törlése

3. **51+ másodperc**: Normál működés
   - NAV worker elindul
   - SAVE/UPDATE/DELETE folyamatosan működik
   - Tabok folyamatos szkennelése

### Törlési Folyamat (Deletion Process)

Amikor egy ID törlésre kerül:

When an ID is deleted:

```python
schedule_delete(tid)
  ↓
_pending_delete_buffer.append(tid)
  ↓
flush_pending_deletes()
  ↓
dispatcher.enqueue_delete(tid)
  ↓
DELETE API hívás a szerverhez (DELETE API call to server)
  ↓
process_dispatcher_results()
  ↓
"delete_ok" esetén:
  - active_ids.remove(tid)
  - save_active_all(active_ids)  ← active_ids.txt frissül
  - seen.remove(tid)
  - remove_seen_line(tid)         ← seen_ids.txt frissül
```

## Tesztelés (Testing)

### Manuális Teszt (Manual Test)

A kód működésének ellenőrzéséhez:

To verify the code works:

1. Készíts egy active_ids.txt fájlt néhány "halott" ID-val
2. Indítsd el a scriptet
3. Várj 50 másodpercet
4. Ellenőrizd a logokat:
   ```
   🧹 BOOTSTRAP fázis befejeződött – indítás utáni cleanup elindítva...
   🔄 TAB-RESYNC indul (nyitott MAIN/GROUP/NEXT tabok alapján)…
   🗑️ TAB-RESYNC: X ID már nem él → törlés Supabase + txt
   ❌ DELETE kész: [ID] cid=[correlation_id]
   ✅ Bootstrap utáni cleanup befejezve
   ```
5. Ellenőrizd az active_ids.txt fájlt - a halott ID-k törlődtek

### Várható Log Kimenet (Expected Log Output)

```
[11:44:35] 🚀 BOOTSTRAP fázis indul: az első 50 mp-ben csak main/group/next tab nyitás + tbody ID gyűjtés, nincs SAVE/UPDATE/DELETE/NAV.
...
[11:45:25] 🧹 BOOTSTRAP fázis befejeződött – indítás utáni cleanup elindítva...
[11:45:25] 🔄 TAB-RESYNC indul (nyitott MAIN/GROUP/NEXT tabok alapján)…
[11:45:25] collect_live_ids_from_open_tabs: 42 élő tbody ID a nyitott tabokból
[11:45:25] 🗑️ TAB-RESYNC: 5 ID már nem él → törlés Supabase + txt
[11:45:26] ❌ DELETE kész: old_id_1 cid=abc-123
[11:45:26] ❌ DELETE kész: old_id_2 cid=def-456
...
[11:45:26] ✅ Bootstrap utáni cleanup befejezve
[11:45:26] 🔁 TAB-RESYNC kész.
[11:45:26] 🚀 NAV háttér worker elindítva (BOOTSTRAP után)
```

## Biztonság (Safety)

A megoldás biztonságos, mert:

The solution is safe because:

1. **Egyszer fut**: `bootstrap_cleanup_done` flag biztosítja, hogy csak 1× fut
2. **Kivételkezelés**: Ha hiba van, a flag így is beállításra kerül
3. **Bootstrap védelem**: `schedule_delete()` nem enged törlést bootstrap alatt
4. **Csak nyitott tabok**: Csak a ténylegesen nyitott tabok ID-it nézi
5. **Flush garantált**: A törlések azonnal kiküldésre kerülnek

## Megjegyzések (Notes)

- A cleanup a NAV worker indítása ELŐTT fut
- A cleanup után a rendszer tovább működik normál módban
- Ha nincs élő ID (friss indításnál), ez normális és logolva van
- A törlések aszinkron módon történnek a dispatcher-en keresztül

---

**Implementáció dátuma**: 2026-01-20
**Fájl**: lopasNEW - 0109.py
**Sorok**: 3937-3938, 4206-4216
