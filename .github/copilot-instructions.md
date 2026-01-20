# Copilot Instructions for kod2026

## Project Overview

This repository contains an automated betting arbitrage system (surebet scraper) that monitors multiple betting sites for profitable arbitrage opportunities. The system uses Selenium with undetected Chrome driver to scrape betting data and manage concurrent browser tabs.

## Tech Stack

- **Python 3.x** with the following key dependencies:
  - `selenium` - Web automation
  - `undetected-chromedriver` - Chrome driver wrapper to avoid detection
  - `requests` - HTTP client for API calls
  - Chrome WebDriver (version 143)
- **Supabase** - Cloud database and Edge Functions for data storage
- **Chrome DevTools Protocol (CDP)** - For advanced browser automation

## Commands

### Running the Application

```bash
# Run with default account (acc1)
python "lopasNEW - 0109.py"

# Run with specific account
python "lopasNEW - 0109.py" --acc=acc1
python "lopasNEW - 0109.py" --acc=acc2

# Or use environment variable
SB_ACTIVE_ACCOUNT=acc2 python "lopasNEW - 0109.py"
```

### Environment Variables

- `SB_USER` - Override account email
- `SB_PASS` - Override account password
- `SB_ACTIVE_ACCOUNT` - Select account (acc1 or acc2)
- `DEBUG_HTTP` - Enable HTTP request/response logging (0 or 1)
- `SB_ACCOUNT_ROTATE_MIN` - Minutes between account rotation (default: 32)

## Project Structure

```
kod2026/
├── lopasNEW - 0109.py      # Main application script
├── .github/
│   └── copilot-instructions.md
├── profile_surebet_acc1/   # Chrome profile directory for account 1
├── profile_surebet_acc2/   # Chrome profile directory for account 2
├── seen_ids.txt           # Tracked betting IDs (timestamp | id)
├── active_ids.txt         # Currently active betting IDs
├── found_links.txt        # Log of discovered betting links
└── link_cache.json        # Cached betting links for fast lookup
```

## Code Style and Conventions

### General Guidelines

1. **Follow existing patterns** - The codebase uses a specific structure with global variables and function naming conventions
2. **Error handling** - Always wrap driver operations in try-except blocks to handle connection errors gracefully
3. **Logging** - Use `log()` for informational messages and `warn()` for errors/warnings
4. **Thread safety** - Be careful with shared state (global variables) in multi-threaded contexts

### Naming Conventions

- **Private/internal functions**: Prefix with underscore (e.g., `_safe_execute_script`, `_inject_disable_animations`)
- **Global constants**: UPPERCASE_WITH_UNDERSCORES (e.g., `MAIN_URL`, `CHECK_INTERVAL`)
- **Config variables**: Descriptive names with units when applicable (e.g., `TAB_CLEANUP_INTERVAL`, `DISAPPEAR_GRACE_SEC`)
- **Thread workers**: Suffix with `_worker` (e.g., `background_nav_worker`, `tab_cleanup_worker`)

### Selenium Best Practices

1. **Always use safe wrappers** for driver operations:
   - `_safe_execute_script()` instead of `driver.execute_script()`
   - `_safe_cdp_cmd()` instead of `driver.execute_cdp_cmd()`
   - `_safe_window_handles()` instead of `driver.window_handles`

2. **Handle stale elements**: Retry operations that may encounter `StaleElementReferenceException`

3. **Check DRIVER_DEAD flag**: Before any driver operation, check if `DRIVER_DEAD` is True

4. **Window management**: Always store original window handle and restore it after switching tabs

### CDP (Chrome DevTools Protocol) Usage

- Use CDP for performance-critical operations (keyboard input, network inspection, target management)
- Always handle CDP errors gracefully with the `_safe_cdp_cmd()` wrapper
- CDP commands are used for:
  - Network control and blocking
  - Script injection on page load
  - Keyboard event simulation
  - Target/tab management

### Threading Patterns

The application uses several background workers:
- **NAV worker**: Resolves betting links asynchronously
- **Group/Next opener worker**: Opens new tabs without blocking main loop
- **Tab cleanup worker**: Periodically closes stray tabs
- **HTTP dispatcher**: Handles async HTTP requests with batching

When adding new workers:
1. Use `daemon=True` for background threads
2. Check `DRIVER_DEAD` flag in worker loops
3. Handle exceptions gracefully and log errors
4. Use `Queue` for thread-safe communication

### HTTP/API Guidelines

1. **Supabase Edge Functions**: Use the dispatcher pattern for async operations
   - `dispatcher.enqueue_save()` - For new records
   - `dispatcher.enqueue_update()` - For updates
   - `dispatcher.enqueue_delete()` - For deletions

2. **Batching**: Updates and deletes are automatically batched for efficiency

3. **Correlation IDs**: Always include X-Correlation-Id header for tracing

4. **Error handling**: Check response status and handle duplicates, 404s, etc.

### State Management

- **Bootstrap phase**: First ~50 seconds after start, only collect IDs, no saves/updates/deletes
- **Active IDs**: Maintain in memory (`active_ids`) and persist to `active_ids.txt`
- **Seen IDs**: Track in `seen` set and `seen_ids.txt` with timestamps
- **Link cache**: Store resolved betting links in `link_cache.json` for fast lookup

## Testing

Currently, this project does not have automated tests. When adding features:
1. Test manually with both `--acc=acc1` and `--acc=acc2`
2. Verify login works correctly
3. Check that tab management doesn't leak memory
4. Ensure HTTP requests are properly batched
5. Validate that account rotation works as expected

## Git Workflow

1. **Branch naming**: Use descriptive names (e.g., `feature/add-new-bookmaker`, `fix/tab-cleanup-bug`)
2. **Commit messages**: Write clear, concise messages describing what changed and why
3. **No secrets**: Never commit credentials, API keys, or tokens to the repository
4. **Profile directories**: The `profile_surebet_acc1/` and `profile_surebet_acc2/` directories are for local Chrome profiles and should not be committed

## Security Considerations

1. **Credentials**: Never hardcode credentials. Use environment variables or the existing ACCOUNTS configuration
2. **API keys**: The Supabase anon key is already in the code, but sensitive keys should be in environment variables
3. **CDP security**: Be cautious when injecting scripts into pages
4. **Rate limiting**: The system includes built-in delays and account rotation to avoid detection

## Boundaries and Restrictions

### DO NOT modify:
- Chrome profile directories (`profile_surebet_acc1/`, `profile_surebet_acc2/`)
- Runtime state files (`seen_ids.txt`, `active_ids.txt`) unless implementing state management changes
- The core login flow without extensive testing
- CDP configuration that prevents detection

### DO modify with care:
- Timing constants (TEST thoroughly to avoid detection or performance issues)
- Thread worker logic (ensure proper error handling and cleanup)
- Selenium selectors (betting sites may change their HTML structure)
- HTTP batching thresholds (affects API load)

### ALWAYS:
- Test changes with real browser automation
- Verify that the bootstrap phase works correctly
- Ensure proper cleanup on shutdown (flush pending operations)
- Check that memory usage remains reasonable with multiple tabs

## Performance Considerations

1. **Tab management**: The system maintains multiple browser tabs (main, group pages, next pages). Excessive tabs can cause memory issues.
2. **CDP polling**: Target inspection happens every ~150ms, adjust `CDP_POLL_INTERVAL` if needed
3. **Bootstrap phase**: Initial data collection takes ~50 seconds, don't skip this
4. **Batching**: HTTP operations are batched to reduce API calls
5. **Account rotation**: System restarts every ~32 minutes to rotate accounts

## Common Pitfalls to Avoid

1. **Don't use blocking operations in the main loop** - Use async workers
2. **Don't forget to restore window handles** after switching tabs
3. **Don't skip error handling** for driver operations - Chrome can crash
4. **Don't modify state files directly** - Use the provided functions
5. **Don't add console.log statements** to injected scripts - they pollute logs
6. **Don't remove animation disabling** - it improves performance
7. **Don't disable the DRIVER_DEAD checks** - they prevent crashes during shutdown

## Additional Notes

- The system is designed to run 24/7 with automatic account rotation
- Memory leaks are prevented by periodic tab cleanup
- The bootstrap phase is critical for stable operation
- CDP is used extensively for performance and anti-detection
- All HTTP operations are non-blocking and batched for efficiency
