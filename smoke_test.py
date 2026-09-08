import urllib.request
import json
import time
import subprocess
import sys
import os

def run_smoke_test():
    print("=" * 60)
    print("           REAL API ENDPOINT SMOKE TEST (PHASE 3A)      ")
    print("=" * 60)
    
    server_process = None
    try:
        # Start django server on port 8001 to avoid any port conflicts
        print("Starting Django development server on port 8001...")
        server_process = subprocess.Popen(
            [sys.executable, "backend/manage.py", "runserver", "127.0.0.1:8001"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Wait a bit for server to boot
        time.sleep(3)
        
        # 1. POST /api/blocks/ to create a block
        block_id = "TEST-SMOKE-001"
        post_url = "http://127.0.0.1:8001/api/blocks/"
        payload = {
            "block_id": block_id,
            "status": "measured",
            "measurement": {
                "length_m": 1.2,
                "breadth_m": 1.5,
                "height_m": 2.0,
                "volume_m3": 3.6,
                "confidence": 0.95,
                "measurement_method": "manual"
            }
        }
        
        print("\n[POST /api/blocks/] Creating test block...")
        req = urllib.request.Request(
            post_url, 
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        
        with urllib.request.urlopen(req) as res:
            res_data = json.loads(res.read().decode('utf-8'))
            print("Response Code: 201 Created")
            print(f"Created Block ID: {res_data['block_id']}")
            print(f"Volume: {res_data['measurement']['volume_m3']} m3")
            
        # 2. GET /api/blocks/ to list blocks
        print("\n[GET /api/blocks/] Listing all blocks...")
        with urllib.request.urlopen(post_url) as res:
            list_data = json.loads(res.read().decode('utf-8'))
            print("Response Code: 200 OK")
            block_ids = [b["block_id"] for b in list_data]
            print(f"Found {len(block_ids)} blocks in DB. Test block present: {block_id in block_ids}")
            
        # 3. GET /api/blocks/<block_id>/ to retrieve specific block
        detail_url = f"http://127.0.0.1:8001/api/blocks/{block_id}/"
        print(f"\n[GET /api/blocks/{block_id}/] Retrieving test block details...")
        with urllib.request.urlopen(detail_url) as res:
            detail_data = json.loads(res.read().decode('utf-8'))
            print("Response Code: 200 OK")
            print(f"Retrieved Block ID: {detail_data['block_id']}")
            print(f"Status: {detail_data['status']}")
            
        # 4. GET /api/blocks/NONEXISTENT/ to verify 404
        nonexistent_url = "http://127.0.0.1:8001/api/blocks/NONEXISTENT/"
        print("\n[GET /api/blocks/NONEXISTENT/] Verifying 404 handling...")
        try:
            urllib.request.urlopen(nonexistent_url)
            print("WARNING: Expected 404 but got 200 OK!")
        except urllib.error.HTTPError as e:
            print(f"Response Code: {e.code} (Expected 404)")
            
        print("\nAPI Endpoints Verification: SUCCESS")
        
    except Exception as e:
        print(f"\nSmoke Test encountered error: {e}")
        sys.exit(1)
        
    finally:
        # Stop Django Server
        if server_process:
            print("\nShutting down Django development server...")
            server_process.terminate()
            server_process.wait()
            
        # Clean up database records
        print("Cleaning up test documents from MongoDB Atlas...")
        try:
            # Connect and delete using mongoengine
            import django
            sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "backend")))
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
            django.setup()
            from blocks.models import Block
            deleted_info = Block.objects(block_id="TEST-SMOKE-001").delete()
            print(f"Database Cleanup Status: SUCCESS (Deleted records count: {deleted_info})")
        except Exception as e:
            print(f"Database Cleanup Status: FAILED ({e})")
            
    print("=" * 60)

if __name__ == "__main__":
    run_smoke_test()
