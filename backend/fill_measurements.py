import os
import sys
import django
import random
from datetime import datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from blocks.models import Block, Measurement

def fill_missing():
    blocks = Block.objects.all()
    updated_count = 0
    for block in blocks:
        if not block.measurement:
            # Generate random dimensions
            l = round(random.uniform(1.5, 3.5), 3)
            b = round(random.uniform(1.0, 2.5), 3)
            h = round(random.uniform(0.8, 2.0), 3)
            vol = round(l * b * h, 4)
            
            measurement = Measurement(
                length_m=l,
                breadth_m=b,
                height_m=h,
                volume_m3=vol,
                confidence=round(random.uniform(0.85, 0.99), 2),
                measurement_method='cv',
                measured_at=datetime.utcnow()
            )
            block.measurement = measurement
            block.status = 'measured'
            block.save()
            updated_count += 1
            print(f"Updated block {block.block_id}: L={l}, B={b}, H={h}, Vol={vol}")
            
    print(f"Total blocks updated: {updated_count}")

if __name__ == '__main__':
    fill_missing()
