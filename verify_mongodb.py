import sys
import os
import django

# Setup Django environment
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "backend")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
django.setup()

from mongoengine.connection import get_db, get_connection
from blocks.models import Quarry, Block, Measurement, Assessment
import datetime

def main():
    print("=" * 60)
    print("        MONGODB PERSISTENCE VERIFICATION SYSTEM        ")
    print("=" * 60)

    # Ensure MONGODB_URI is set
    mongodb_uri = os.environ.get('MONGODB_URI')
    if not mongodb_uri:
        print("MongoDB Connection Status: FAILED")
        print("Reason: Environment variable MONGODB_URI is missing or not configured.")
        print("Please configure MONGODB_URI in your environment or in a .env file.")
        print("=" * 60)
        sys.exit(1)

    # 1. Check Connection status
    try:
        conn = get_connection()
        db = get_db()
        db_name = db.name
        # Test connection by pinging
        conn.admin.command('ping')
        print(f"MongoDB Connection Status: SUCCESS")
        print(f"Database Name:             {db_name}")
    except Exception as e:
        error_msg = str(e)
        # Obfuscate password or sensitive details in error
        if "mongodb+srv://" in error_msg:
            # Simple string replace for security
            import re
            error_msg = re.sub(r'mongodb\+srv://[^@\s]+@', 'mongodb+srv://<REDACTED_CREDENTIALS>@', error_msg)
        print(f"MongoDB Connection Status: FAILED")
        print(f"Error Details:             {error_msg}")
        print("\nReason: Unable to reach MongoDB Atlas with the provided connection URI.")
        print("=" * 60)
        sys.exit(1)

    # 2. CRUD Verification
    quarry_created = False
    block_created = False
    assessment_created = False
    
    temp_quarry = None
    temp_block = None
    temp_assessment = None

    try:
        # Create Quarry
        temp_quarry = Quarry(name="Temp Verification Quarry", location="Verification Zone")
        temp_quarry.save()
        quarry_created = True
        print("Test Quarry Creation:      SUCCESS")

        # Create Measurement (Embedded)
        temp_measurement = Measurement(
            length_m=1.0,
            breadth_m=1.0,
            height_m=1.0,
            volume_m3=1.0,
            confidence=1.0,
            measurement_method="manual"
        )

        # Create Block
        temp_block = Block(
            block_id="TEMP-VERIFY-001",
            quarry=temp_quarry,
            status="measured",
            measurement=temp_measurement
        )
        temp_block.save()
        block_created = True
        print("Test Block Creation:       SUCCESS")

        # Create Assessment
        temp_assessment = Assessment(
            block=temp_block,
            granite_category="Standard",
            gangsaw_classification="Gangsaw Size",
            volume_m3=1.0,
            weight_mt=2.7,
            rate_per_mt=2200.0,
            indicative_seigniorage=5940.0,
            status="draft"
        )
        temp_assessment.save()
        assessment_created = True
        print("Test Assessment Creation:   SUCCESS")

        # Read Back Block
        retrieved_block = Block.objects(block_id="TEMP-VERIFY-001").first()
        if retrieved_block:
            print("Test Document Retrieval:   SUCCESS")
            print("\n--- Retrieved Document Values ---")
            print(f"  Block ID:    {retrieved_block.block_id}")
            print(f"  Quarry Name: {retrieved_block.quarry.name}")
            print(f"  Volume (m3): {retrieved_block.measurement.volume_m3}")
            print(f"  Status:      {retrieved_block.status}")
        else:
            print("Test Document Retrieval:   FAILED (Not found)")

    except Exception as e:
        print(f"CRUD Verification:         FAILED")
        print(f"Error Details:             {e}")
    finally:
        # Cleanup
        print("\n--- Starting Cleanup ---")
        cleanup_success = True
        if assessment_created and temp_assessment:
            try:
                temp_assessment.delete()
                print("Deleted Test Assessment:    SUCCESS")
            except Exception as e:
                print(f"Deleted Test Assessment:    FAILED ({e})")
                cleanup_success = False
        if block_created and temp_block:
            try:
                temp_block.delete()
                print("Deleted Test Block:         SUCCESS")
            except Exception as e:
                print(f"Deleted Test Block:         FAILED ({e})")
                cleanup_success = False
        if quarry_created and temp_quarry:
            try:
                temp_quarry.delete()
                print("Deleted Test Quarry:        SUCCESS")
            except Exception as e:
                print(f"Deleted Test Quarry:        FAILED ({e})")
                cleanup_success = False
                
        if cleanup_success:
            print("Cleanup Status:            SUCCESS")
        else:
            print("Cleanup Status:            WARNING/FAILED")
            
    print("=" * 60)

if __name__ == "__main__":
    main()
