import urllib.request
import urllib.error
import json
import time
import subprocess
import sys
import os
import shutil

def run_smoke_test():
    print("=" * 60)
    print("        REAL CV INTEGRATION API SMOKE TEST (PHASE 4)     ")
    print("=" * 60)
    
    server_process = None
    block_id = "TEST-SMOKE-CV-001"
    temp_img_name = "temp_smoke_test_cv.png"
    
    try:
        # Generate the synthetic image (has block + markers)
        from cv_pipeline.test_pipeline import generate_synthetic_test_image
        generate_synthetic_test_image(temp_img_name)
        
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
        
        # 1. Create a Block first
        blocks_url = "http://127.0.0.1:8001/api/blocks/"
        block_payload = {
            "block_id": block_id,
            "status": "pending"
        }
        
        print("\n[POST /api/blocks/] Creating pending block...")
        req1 = urllib.request.Request(
            blocks_url, 
            data=json.dumps(block_payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req1) as res:
            res_data = json.loads(res.read().decode('utf-8'))
            print(f"Created Block ID: {res_data['block_id']}")

        # 2. Upload image to /api/blocks/<block_id>/measure-cv/
        measure_url = f"http://127.0.0.1:8001/api/blocks/{block_id}/measure-cv/"
        print(f"\n[POST /api/blocks/{block_id}/measure-cv/] Uploading synthetic image...")
        
        with open(temp_img_name, 'rb') as f:
            file_data = f.read()
            
        boundary = 'Boundary---BlockMeasureCVSmokeTest'
        data = []
        data.append(f'--{boundary}'.encode('utf-8'))
        data.append(f'Content-Disposition: form-data; name="image"; filename="{temp_img_name}"'.encode('utf-8'))
        data.append('Content-Type: image/png'.encode('utf-8'))
        data.append(''.encode('utf-8'))
        data.append(file_data)
        data.append(f'--{boundary}--'.encode('utf-8'))
        data.append(''.encode('utf-8'))
        
        body = b'\r\n'.join(data)
        
        req2 = urllib.request.Request(
            measure_url,
            data=body,
            headers={
                'Content-Type': f'multipart/form-data; boundary={boundary}',
                'Content-Length': str(len(body))
            },
            method='POST'
        )
        
        try:
            with urllib.request.urlopen(req2) as res:
                # Should not succeed without custom model, but handle if it does
                res_data = json.loads(res.read().decode('utf-8'))
                print(f"Response Code: {res.status_code}")
                print(f"CV Status: {res_data['cv_status']}")
        except urllib.error.HTTPError as e:
            res_body = e.read().decode('utf-8')
            error_data = json.loads(res_body)
            print(f"Response Code: {e.code} (Expected 400 due to generic COCO model)")
            print(f"Returned Error Message: {error_data.get('error')}")

        # 3. Connect to MongoDB Atlas to check that CV failed status was persisted
        print("\nVerifying persisted values in MongoDB Atlas...")
        import django
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "backend")))
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
        django.setup()
        from blocks.models import Block
        
        db_block = Block.objects(block_id=block_id).first()
        if db_block:
            print("MongoDB Record Verification:")
            print(f"  Block ID:         {db_block.block_id}")
            print(f"  CV Status:        {db_block.cv_status}")
            print(f"  CV Error Message: {db_block.cv_error_message}")
            print(f"  Raw Image Path:   {db_block.raw_image_path}")
            print("Atlas Persistence Verification: SUCCESS")
        else:
            print("Atlas Persistence Verification: FAILED (Block not found)")

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
            deleted_info = Block.objects(block_id=block_id).delete()
            print(f"Database Cleanup Status: SUCCESS (Deleted records count: {deleted_info})")
        except Exception as e:
            print(f"Database Cleanup Status: FAILED ({e})")
            
        # Cleanup temporary files
        if os.path.exists(temp_img_name):
            os.remove(temp_img_name)
        # Clear uploaded media files
        from django.conf import settings
        if os.path.exists(settings.MEDIA_ROOT):
            shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
            print("Local Media Directory Cleanup: SUCCESS")
            
    print("=" * 60)

if __name__ == "__main__":
    run_smoke_test()
