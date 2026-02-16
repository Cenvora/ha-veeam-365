#!/usr/bin/env python
"""Check VeeamClient API methods."""

from veeam_365.client import VeeamClient
import inspect

print("=== VeeamClient Public Methods ===")
methods = [m for m in dir(VeeamClient) if not m.startswith('_')]
for method in methods:
    attr = getattr(VeeamClient, method)
    if callable(attr):
        print(f"  {method}()")
    else:
        print(f"  {method}")

print("\n=== Checking api() method signature ===")
sig = inspect.signature(VeeamClient.api)
print(f"api{sig}")

print("\n=== Example: api('job') return type ===")
# Let's check what api namespace returns
import sys
sys.path.insert(0, 'c:\\Users\\jonah\\source\\repos\\ha-veeam-365')

try:
    client = VeeamClient(
        host="https://localhost:4443",
        username="test",
        password="test",
        api_version="v1_3_rev1",
        verify_ssl=False
    )
    job_api = client.api("job")
    print(f"Type: {type(job_api)}")
    print(f"Methods: {[m for m in dir(job_api) if not m.startswith('_')]}")
except Exception as e:
    print(f"Error: {e}")
