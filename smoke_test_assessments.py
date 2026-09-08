import urllib.request
import json
import time
import subprocess
import sys
import os

def run_smoke_test():
    print("=" * 60)
    print("      REAL ASSESSMENT API ENDPOINT SMOKE TEST (PHASE 3B) ")
    print("=" * 60)
    
    server_process = None
    try:
        # Start django server on port 8001
        print("Starting Django development server on port 8001...")
        server_process = subprocess.Popen(
            [sys.executable, "backend/manage.py", "runserver", "127.0.0.1:8001"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Wait a bit for server to boot
        time.sleep(3)
        
        # 1. Create a measured Block first
        block_id = "TEST-SMOKE-ASS-001"
        blocks_url = "http://127.0.0.1:8001/api/blocks/"
        block_payload = {
            "block_id": block_id,
            "status": "measured",
            "measurement": {
                "length_m": 1.54,
                "breadth_m": 1.60,
                "height_m": 2.90,
                "volume_m3": 7.1456,
                "confidence": 0.95,
                "measurement_method": "manual"
            }
        }
        
        print("\n[POST /api/blocks/] Creating measured block...")
        req1 = urllib.request.Request(
            blocks_url, 
            data=json.dumps(block_payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req1) as res:
            res_data = json.loads(res.read().decode('utf-8'))
            print(f"Created Block ID: {res_data['block_id']}")

        # 2. POST /api/assessments/ to trigger and save calculations
        assessments_url = "http://127.0.0.1:8001/api/assessments/"
        ass_payload = {
            "block_id": block_id,
            "granite_category": "Premium",
            "gangsaw_classification": "Gangsaw Size",
            "density": 2.7
        }
        
        print("\n[POST /api/assessments/] Creating assessment...")
        req2 = urllib.request.Request(
            assessments_url,
            data=json.dumps(ass_payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req2) as res:
            ass_data = json.loads(res.read().decode('utf-8'))
            print("Response Code: 201 Created")
            print(f"Block ID:               {ass_data['block_id']}")
            print(f"Volume (m3):            {ass_data['volume_m3']}")
            print(f"Estimated Weight (MT):  {ass_data['weight_mt']}")
            print(f"Density:                {ass_data['density_mt_per_m3']} (Snapshot)")
            print(f"Rate per MT:            {ass_data['rate_per_mt']}")
            print(f"Indicative Seigniorage: {ass_data['indicative_seigniorage']}")
            
        # 3. GET /api/assessments/ to list all assessments
        print("\n[GET /api/assessments/] Listing all assessments...")
        with urllib.request.urlopen(assessments_url) as res:
            list_data = json.loads(res.read().decode('utf-8'))
            print("Response Code: 200 OK")
            block_ids = [ass["block_id"] for ass in list_data]
            print(f"Found {len(block_ids)} assessments. Test block assessed: {block_id in block_ids}")
            
        # 4. GET /api/assessments/<block_id>/ to retrieve specific assessment
        detail_url = f"http://127.0.0.1:8001/api/assessments/{block_id}/"
        print(f"\n[GET /api/assessments/{block_id}/] Retrieving specific assessment...")
        with urllib.request.urlopen(detail_url) as res:
            detail_data = json.loads(res.read().decode('utf-8'))
            print("Response Code: 200 OK")
            print(f"Retrieved Block ID:      {detail_data['block_id']}")
            print(f"Granite Category:        {detail_data['granite_category']}")
            print(f"Indicative Seigniorage: {detail_data['indicative_seigniorage']}")
            
        print("\nAssessment API Endpoints Verification: SUCCESS")
        
    except Exception as e:
        print(f"\nSmoke Test encountered error: {e}")
        sys.exit(1)
        
    finally:
        # Stop Django Server
        if server_process:
            print("\nShutting down Django development server...")
            server_process.terminate()
            server_process.wait()
            
        # Clean up database records (will cascade to Assessment)
        print("Cleaning up test documents from MongoDB Atlas...")
        try:
            import django
            sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "backend")))
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
            django.setup()
            from blocks.models import Block
            deleted_info = Block.objects(block_id="TEST-SMOKE-ASS-001").delete()
            print(f"Database Cleanup Status: SUCCESS (Deleted records count: {deleted_info})")
        except Exception as e:
            print(f"Database Cleanup Status: FAILED ({e})")
            
    print("=" * 60)

if __name__ == "__main__":
    run_smoke_test()
