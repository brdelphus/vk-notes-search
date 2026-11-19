# Example note

Delete this once you have your own notes. It exists so `vk.py index` has
something to chew on out of the box.

## The problem

Something broke in a way that took an hour to figure out, and the fix was three
lines. Without writing it down, the next occurrence costs another hour.

## The fix

```bash
# whatever actually resolved it
systemctl restart the-thing
```

## Why it works

The service keeps a stale handle to the old socket until restarted, so reloading
the config alone is not enough — the process has to be replaced.
