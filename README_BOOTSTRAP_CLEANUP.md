# Bootstrap Cleanup Feature - README

## Magyar nyelvű összefoglaló (Hungarian Summary)

### Mi változott?

A kód most automatikusan végrehajtja a tisztítási folyamatot (cleanup) az első 50 másodperces bootstrap fázis után.

### Hogyan működik?

1. **0-50 másodperc**: Bootstrap fázis
   - A script elindul és bejelentkezik
   - Megnyitja a főoldalt (MAIN), GROUP oldalakat, és NEXT oldalakat
   - Összegyűjti az összes látható tbody ID-t
   - **FONTOS**: Ebben az időszakban NEM történik mentés/frissítés/törlés a szerveren

2. **~50-51 másodperc**: Automatikus tisztítás
   - A script automatikusan meghívja a `full_resync_and_cleanup()` függvényt
   - Végigszkeneli az összes nyitott tabot (MAIN, GROUP, NEXT)
   - Összegyűjti az élő tbody ID-kat
   - Összehasonlítja őket az `active_ids.txt` fájlban lévő ID-kkal
   - **Törlés**: Azok az ID-k, amelyek az `active_ids.txt`-ben vannak, de már nem találhatók meg egyik nyitott tabon sem, törlésre kerülnek:
     - Törlődnek a szerverről (DELETE API hívás)
     - Törlődnek az `active_ids.txt` fájlból
     - Törlődnek a `seen_ids.txt` fájlból is

3. **51+ másodperc**: Normál működés
   - A NAV worker elindul
   - A mentés/frissítés/törlés működik normálisan
   - A rendszer folyamatosan figyeli az ID-kat

### Példa

**Indítás előtt** - `active_ids.txt`:
```
old_id_1
old_id_2
old_id_3
live_id_1
live_id_2
```

**50 másodperc után** - Nyitott tabokon található ID-k:
- MAIN: `live_id_1`, `live_id_2`, `live_id_6`
- GROUP: `live_id_3`
- NEXT: `live_id_5`

**Cleanup után** - `active_ids.txt`:
```
live_id_1
live_id_2
live_id_3
live_id_5
live_id_6
```

A következők törlődtek (mert nem voltak a tabokon):
- `old_id_1`
- `old_id_2`
- `old_id_3`

### Log üzenetek

A következő log üzeneteket fogod látni:

```
[11:45:25] 🧹 BOOTSTRAP fázis befejeződött – indítás utáni cleanup elindítva...
[11:45:25] 🔄 TAB-RESYNC indul (nyitott MAIN/GROUP/NEXT tabok alapján)…
[11:45:25] collect_live_ids_from_open_tabs: 6 élő tbody ID a nyitott tabokból
[11:45:25] 🗑️ TAB-RESYNC: 3 ID már nem él → törlés Supabase + txt
[11:45:26] ❌ DELETE kész: old_id_1 cid=abc-123
[11:45:26] ❌ DELETE kész: old_id_2 cid=def-456
[11:45:26] ❌ DELETE kész: old_id_3 cid=ghi-789
[11:45:26] ✅ Bootstrap utáni cleanup befejezve
[11:45:26] 🔁 TAB-RESYNC kész.
```

---

## English Summary

### What Changed?

The code now automatically executes a cleanup process after the first 50-second bootstrap phase.

### How It Works?

1. **0-50 seconds**: Bootstrap phase
   - Script starts and logs in
   - Opens main page, GROUP pages, and NEXT pages
   - Collects all visible tbody IDs
   - **IMPORTANT**: During this time NO save/update/delete operations happen on the server

2. **~50-51 seconds**: Automatic cleanup
   - Script automatically calls the `full_resync_and_cleanup()` function
   - Scans all open tabs (MAIN, GROUP, NEXT)
   - Collects live tbody IDs
   - Compares them with IDs in `active_ids.txt` file
   - **Deletion**: IDs that are in `active_ids.txt` but not found in any open tab are deleted:
     - Deleted from server (DELETE API call)
     - Deleted from `active_ids.txt` file
     - Deleted from `seen_ids.txt` file as well

3. **51+ seconds**: Normal operation
   - NAV worker starts
   - Save/update/delete operations work normally
   - System continuously monitors IDs

### Example

**Before start** - `active_ids.txt`:
```
old_id_1
old_id_2
old_id_3
live_id_1
live_id_2
```

**After 50 seconds** - IDs found on open tabs:
- MAIN: `live_id_1`, `live_id_2`, `live_id_6`
- GROUP: `live_id_3`
- NEXT: `live_id_5`

**After cleanup** - `active_ids.txt`:
```
live_id_1
live_id_2
live_id_3
live_id_5
live_id_6
```

The following were deleted (because they weren't on the tabs):
- `old_id_1`
- `old_id_2`
- `old_id_3`

### Log Messages

You will see these log messages:

```
[11:45:25] 🧹 BOOTSTRAP fázis befejeződött – indítás utáni cleanup elindítva...
[11:45:25] 🔄 TAB-RESYNC indul (nyitott MAIN/GROUP/NEXT tabok alapján)…
[11:45:25] collect_live_ids_from_open_tabs: 6 élő tbody ID a nyitott tabokból
[11:45:25] 🗑️ TAB-RESYNC: 3 ID már nem él → törlés Supabase + txt
[11:45:26] ❌ DELETE kész: old_id_1 cid=abc-123
[11:45:26] ❌ DELETE kész: old_id_2 cid=def-456
[11:45:26] ❌ DELETE kész: old_id_3 cid=ghi-789
[11:45:26] ✅ Bootstrap utáni cleanup befejezve
[11:45:26] 🔁 TAB-RESYNC kész.
```

---

## Technical Details

### Files Modified

1. **lopasNEW - 0109.py**
   - Added `bootstrap_cleanup_done` flag (line 3937-3938)
   - Added cleanup trigger after bootstrap (lines 4206-4216)

### Code Changes

**Minimal changes made:**
- 2 lines to declare the flag
- 12 lines to trigger the cleanup once after bootstrap

**No existing functionality was broken:**
- The `full_resync_and_cleanup()` function already existed
- The `collect_live_ids_from_open_tabs()` function already existed
- The `schedule_delete()` function already existed
- We only added the automatic trigger

### Safety

- **Runs only once**: The `bootstrap_cleanup_done` flag ensures it runs exactly once
- **Exception handling**: If an error occurs, the flag is still set to prevent retries
- **Bootstrap protection**: `schedule_delete()` won't delete during bootstrap
- **Only open tabs**: Only scans tabs that are actually open
- **Guaranteed flush**: Deletions are immediately sent to the server

### Documentation

Three documentation files were created:

1. **BOOTSTRAP_CLEANUP_IMPLEMENTATION.md** - Detailed implementation guide
2. **BOOTSTRAP_CLEANUP_FLOW.md** - Visual flow diagram with examples
3. **README_BOOTSTRAP_CLEANUP.md** - This file (user-friendly summary)

### Testing

To test manually:
1. Create an `active_ids.txt` file with some "dead" IDs
2. Start the script
3. Wait 50 seconds
4. Check the logs for cleanup messages
5. Verify `active_ids.txt` - the dead IDs should be removed

---

**Implementation Date**: 2026-01-20  
**Author**: GitHub Copilot
