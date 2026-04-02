"""One-shot script: clear target_peers cache entries."""
from src.common.cache import _cache

deleted = 0
for key in list(_cache):
    if "target_peers" in str(key):
        del _cache[key]
        deleted += 1

print(f"Deleted {deleted} target_peers cache entries")
print(f"Remaining cache size: {len(_cache)} entries")
