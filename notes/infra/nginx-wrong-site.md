# Nginx serves the wrong site after a config change

## The problem

Two server blocks on the same port, one with a `server_name` typo. Nginx parses
without complaining, then falls back to the first block it loaded, so requests
land on a completely different site. `nginx -t` says everything is fine.

## The fix

```bash
# list what nginx actually has in memory, not what is on disk
nginx -T | grep -n "server_name"

# then, after fixing, reload rather than restart
systemctl reload nginx
```

## Why it works

`nginx -T` dumps the **parsed** configuration — every included file merged in
load order. That is the only reliable view, because a typo in one `include`
silently changes which `server` block wins the default match.

## Notes

- `reload` keeps existing connections alive; `restart` drops them. For a config
  change, `reload` is almost always what you want.
- No `default_server` marker means the first block on that port is the fallback.
  Mark one explicitly to make the behaviour obvious instead of accidental.
