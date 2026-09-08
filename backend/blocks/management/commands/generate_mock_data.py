import datetime
import random
from django.core.management.base import BaseCommand
from blocks.models import Quarry, Officer, Block, Measurement, Assessment, AuditLog, WeeklyOfficerSummary
from blocks.services import generate_assessment_report

class Command(BaseCommand):
    help = 'Generates idempotent, realistic synthetic POC data for testing and demonstrations.'

    def handle(self, *args, **options):
        self.stdout.write("=" * 60)
        self.stdout.write("          GENERATING SYNTHETIC POC ANALYTICS DATA         ")
        self.stdout.write("=" * 60)

        # 1. Clear previous mock records to ensure idempotency
        self.stdout.write("Clearing previous mock data...")
        deleted_assessments = Assessment.objects(revenue_bucket="POC MOCK REVENUE").delete()
        deleted_blocks = Block.objects(block_id__startswith="MOCK-GR-").delete()
        deleted_officers = Officer.objects(officer_id__startswith="MOCK-OFF-").delete()
        deleted_quarries = Quarry.objects(lessee_name="POC MOCK LESSEE").delete()
        deleted_audits = AuditLog.objects(details__contains="POC MOCK DATA").delete()
        deleted_summaries = WeeklyOfficerSummary.objects(officer_id__startswith="MOCK-OFF-").delete()

        self.stdout.write(f"Cleared: {deleted_quarries} Quarries, {deleted_officers} Officers, {deleted_blocks} Blocks, {deleted_assessments} Assessments, {deleted_audits} Audits, {deleted_summaries} Summaries.")

        # 2. Setup constants
        ap_districts = ["Prakasam", "Nellore", "Chittoor", "Guntur", "Kurnool"]
        granite_categories = ["Premium", "Standard", "Commercial"]
        gangsaw_classifications = ["Gangsaw Size", "Mini Gangsaw Size", "Scabos/Other"]
        override_reasons = [
            "Occluded corner in camera path",
            "Shadow cast by quarry overhead crane",
            "Marker alignment tilted on front surface",
            "Camera perspective distortion on top edge",
            "Dust overlay on side reference marker"
        ]
        lighting_conditions = ["Sunny", "Overcast", "Cloudy", "Indirect Light", "Shadowed"]

        # 6 Weeks time range
        now = datetime.datetime.utcnow()
        start_date = now - datetime.timedelta(weeks=6)

        # 3. Create 8 quarries
        quarries = []
        for i in range(1, 9):
            district = random.choice(ap_districts)
            q = Quarry(
                id=f"MOCK-Q-{1000 + i}",
                name=f"AP Mines - Quarry {chr(64 + i)} (POC MOCK DATA)",
                location=f"Survey No. {100 + i}, Mining Cluster, {district}",
                district=district,
                region="Coastal AP" if district in ["Prakasam", "Nellore", "Guntur"] else "Rayalaseema",
                lessee_name="POC MOCK LESSEE",
                total_area_hectares=float(round(random.uniform(5.0, 35.0), 2)),
                registered_date=start_date - datetime.timedelta(days=365),
                license_expiry_date=now + datetime.timedelta(days=365 * 5)
            )
            q.save()
            quarries.append(q)

        # 4. Create 15 officers
        officers = []
        first_names = ["Anil", "Suresh", "Vijay", "Ramesh", "Kiran", "Madhav", "Venkatesh", "Ram", "Naidu", "Prasad", "Reddy", "Chowdary", "Verma", "Raju", "Srinivas"]
        for i in range(1, 16):
            # Assign to 1-3 random quarries
            assigned = random.sample(quarries, k=random.randint(1, 3))
            o = Officer(
                officer_id=f"MOCK-OFF-{i:03d}",
                name=f"{first_names[i-1]} Kumar (POC MOCK DATA)",
                designation="Assistant Inspector of Mines",
                assigned_quarries=assigned,
                phone=f"+91 98480 {random.randint(10000, 99999)}",
                email=f"{first_names[i-1].lower()}@apmines.gov.in",
                joined_date=start_date - datetime.timedelta(days=random.randint(30, 200)),
                active_status=True
            )
            o.save()
            officers.append(o)

        # 5. Create 180 Blocks (between 150 and 250)
        blocks = []
        assessments_count = 0
        overrides_count = 0
        approvals_count = 0
        audits_count = 0
        total_seigniorage = 0.0

        for idx in range(1, 181):
            block_id = f"MOCK-GR-{1000 + idx}"
            quarry = random.choice(quarries)
            officer = random.choice([o for o in officers if quarry in o.assigned_quarries] or officers)

            # Captured date spread over 6 weeks
            capture_days_ago = random.randint(0, 42)
            captured_at = now - datetime.timedelta(days=capture_days_ago, hours=random.randint(0, 23))

            # Sizing parameters
            length = round(random.uniform(1.2, 2.8), 2)
            breadth = round(random.uniform(1.0, 2.0), 2)
            height = round(random.uniform(1.4, 3.2), 2)
            volume = round(length * breadth * height, 4)

            # Measurements embedded object
            is_override = random.random() < 0.18 # 18% override rate
            confidence = round(random.uniform(0.85, 0.98), 2) if not is_override else 1.0
            
            # Low confidence records (approx 10% of CV records)
            if not is_override and random.random() < 0.1:
                confidence = round(random.uniform(0.5, 0.72), 2)

            meas = Measurement(
                length_m=length,
                breadth_m=breadth,
                height_m=height,
                volume_m3=volume,
                confidence=confidence,
                measurement_method='cv',
                measured_at=captured_at
            )

            gps_lat = round(random.uniform(14.0, 16.5), 6)
            gps_lon = round(random.uniform(78.5, 80.5), 6)

            # Block instantiation
            b = Block(
                block_id=block_id,
                quarry=quarry,
                image_paths=[f"raw/{block_id}_raw.png", f"annotated/{block_id}_annotated.png"],
                captured_at=captured_at,
                gps_latitude=gps_lat,
                gps_longitude=gps_lon,
                status="assessed",
                measurement=meas,
                cv_status="success" if confidence > 0.6 else "failed",
                cv_error_message="" if confidence > 0.6 else "Edge segmentation threshold failure.",
                raw_image_path=f"raw/{block_id}_raw.png",
                annotated_image_path=f"annotated/{block_id}_annotated.png",
                inspecting_officer_id=officer.officer_id,
                inspection_duration_seconds=random.randint(60, 300),
                capture_attempt_count=random.choices([1, 2, 3], weights=[85, 10, 5])[0],
                lighting_condition=random.choice(lighting_conditions),
                device_id=f"DEVICE-{random.randint(100, 999)}"
            )

            # Apply Override
            if is_override:
                overrides_count += 1
                b.is_overridden = True
                b.override_reason = random.choice(override_reasons)
                # Backup original CV measurement
                b.original_measurement = Measurement(
                    length_m=round(length * random.uniform(0.9, 1.1), 2),
                    breadth_m=round(breadth * random.uniform(0.9, 1.1), 2),
                    height_m=round(height * random.uniform(0.9, 1.1), 2),
                    volume_m3=volume,
                    confidence=round(random.uniform(0.8, 0.95), 2),
                    measurement_method='cv',
                    measured_at=captured_at
                )
                b.original_measurement.volume_m3 = round(b.original_measurement.length_m * b.original_measurement.breadth_m * b.original_measurement.height_m, 4)

            # Approval Status
            app_status = random.choices(["approved", "rejected", "pending"], weights=[80, 15, 5])[0]
            b.approval_status = app_status
            if app_status != "pending":
                approvals_count += 1
                b.approved_by = "MOCK-SUPERVISOR"
                b.approved_at = captured_at + datetime.timedelta(hours=random.randint(1, 24))

            b.save()
            blocks.append(b)

            # Generate Audit Logs
            audit = AuditLog(
                block=b,
                action="block_registration",
                actor=officer.name,
                details=f"Block registered at {quarry.name} (POC MOCK DATA)",
                timestamp=captured_at
            )
            audit.save()
            audits_count += 1

            if is_override:
                audit_override = AuditLog(
                    block=b,
                    action="manual_override",
                    actor="MOCK-SUPERVISOR",
                    details=f"Manual override applied. Reason: {b.override_reason} (POC MOCK DATA)",
                    timestamp=b.captured_at + datetime.timedelta(minutes=15)
                )
                audit_override.save()
                audits_count += 1

            if app_status != "pending":
                audit_app = AuditLog(
                    block=b,
                    action=f"block_approval_{app_status}",
                    actor="MOCK-SUPERVISOR",
                    details=f"Block review completed with status: {app_status} (POC MOCK DATA)",
                    timestamp=b.approved_at
                )
                audit_app.save()
                audits_count += 1

            # Generate Assessments
            if app_status == "approved" or (app_status == "pending" and random.random() < 0.5):
                category = random.choice(granite_categories)
                classification = random.choice(gangsaw_classifications)
                density = 2.7

                # Run services report calculation
                report = generate_assessment_report(
                    block_id=block_id,
                    length_m=b.measurement.length_m,
                    breadth_m=b.measurement.breadth_m,
                    height_m=b.measurement.height_m,
                    category=category,
                    classification=classification,
                    density=density
                )

                # Simulated variance calculations
                variance_pct = round(random.uniform(0.5, 7.5), 2)
                if random.random() < 0.08: # 8% outliers
                    variance_pct = round(random.uniform(10.0, 22.0), 2)

                ass = Assessment(
                    block=b,
                    granite_category=category,
                    gangsaw_classification=classification,
                    volume_m3=report['volume_m3'],
                    weight_mt=report['estimated_weight_mt'],
                    rate_per_mt=report['applicable_rate_per_mt'],
                    indicative_seigniorage=report['indicative_seigniorage'],
                    density_mt_per_m3=density,
                    variance_pct=variance_pct,
                    expected_vs_actual_variance_pct=variance_pct,
                    revenue_bucket="POC MOCK REVENUE",
                    assessment_week=int(captured_at.isocalendar()[1]),
                    assessment_month=int(captured_at.month),
                    status="finalized"
                )
                ass.save()
                assessments_count += 1
                total_seigniorage += report['indicative_seigniorage']

        # 6. Generate WeeklyOfficerSummary aggregate data
        self.stdout.write("Generating Weekly Officer summaries...")
        for o in officers:
            for w in range(0, 6):
                week_start = now - datetime.timedelta(weeks=w)
                # Align to Monday
                week_start = week_start - datetime.timedelta(days=week_start.weekday())
                week_start = datetime.datetime(week_start.year, week_start.month, week_start.day, 0, 0, 0)
                
                week_num = int(week_start.isocalendar()[1])
                
                # Fetch blocks inspected by officer in this week
                week_blocks = [b for b in blocks if b.inspecting_officer_id == o.officer_id and int(b.captured_at.isocalendar()[1]) == week_num]
                
                if week_blocks:
                    overridden = [b for b in week_blocks if b.is_overridden]
                    approved = [b for b in week_blocks if b.approval_status == 'approved']
                    
                    blocks_inspected = len(week_blocks)
                    avg_confidence = float(sum(b.measurement.confidence for b in week_blocks) / blocks_inspected)
                    override_cnt = len(overridden)
                    app_rate = float(len(approved) / blocks_inspected) if blocks_inspected > 0 else 0.0
                    avg_duration = float(sum(b.inspection_duration_seconds for b in week_blocks) / blocks_inspected)
                    
                    summary = WeeklyOfficerSummary(
                        officer_id=o.officer_id,
                        week_start_date=week_start,
                        blocks_inspected=blocks_inspected,
                        avg_confidence=round(avg_confidence, 2),
                        override_count=override_cnt,
                        approval_rate=round(app_rate, 2),
                        avg_inspection_duration_seconds=round(avg_duration, 1)
                    )
                    summary.save()

        # 7. Print summary statistics
        self.stdout.write("=" * 60)
        self.stdout.write("         SYNTHETIC POC MOCK DATA GENERATION COMPLETE      ")
        self.stdout.write("=" * 60)
        self.stdout.write(f"Officers Generated:      {len(officers)}")
        self.stdout.write(f"Quarries Generated:      {len(quarries)}")
        self.stdout.write(f"Blocks Inspected:        {len(blocks)}")
        self.stdout.write(f"Assessments Completed:   {assessments_count}")
        self.stdout.write(f"Manual Overrides:        {overrides_count}")
        self.stdout.write(f"Approvals Completed:     {approvals_count}")
        self.stdout.write(f"Timeline Audit Logs:     {audits_count}")
        self.stdout.write(f"Date Range Simulated:    {start_date.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}")
        self.stdout.write(f"Total Indicative Revenue: INR {total_seigniorage:,.2f}")
        self.stdout.write("=" * 60)
