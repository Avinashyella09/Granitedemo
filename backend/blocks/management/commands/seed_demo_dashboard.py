"""
Seed a realistic demonstration dataset for the Supervisor Dashboard.

WHY THIS COMMAND EXISTS
-----------------------
Seven of the eight dashboard tabs read collections that are empty in this
deployment (0 quarries, 0 officers, 0 weekly summaries, 3 assessments). They
are not broken - they have nothing to show. This command supplies the missing
supporting records so the dashboard demonstrates what it will look like once
the system carries enough operational data.

WHAT IT MUST NEVER DO
---------------------
The 24 existing Block documents are READ-ONLY. This command never updates,
renames, re-measures, re-approves or deletes any of them. It asserts that fact
before and after every run (see _snapshot_existing / _assert_registry_intact);
if a single existing block changed, the run aborts.

It is also never destructive. Unlike generate_mock_data.py - which opens by
deleting records and writes the literal string "MOCK" into user-facing fields -
this command only inserts, and every record it inserts is recoverable by an
explicit marker so `--remove` can take them back out again.

HOW DEMO RECORDS ARE IDENTIFIED
-------------------------------
Four markers, chosen so no user-facing text carries them:

    Block.device_id == DEMO_DEVICE_ID      (None on all 24 real blocks)
    Quarry.id       startswith 'APQ-'
    Officer.officer_id startswith 'APMD-'
    Assessment / AuditLog                  -> reached through their demo Block
    WeeklyOfficerSummary.officer_id        startswith 'APMD-'

device_id is the load-bearing one: it is a real schema field, it is not
rendered as a headline value anywhere in the dashboard, and it is empty on
every genuine record, so the demo set can always be separated from real field
data without guessing.

MONEY
-----
Every rupee figure is produced by blocks.seigniorage.calculate() - the official
RTGS engine - from the block's own stored dimensions. No revenue number is
typed in by hand, so quarry revenue, officer revenue and the Revenue Summary
totals reconcile with the measurements by construction.

CACHE
-----
views.py caches analytics for 300s per process and only invalidates on API
writes. This command runs in a different process and cannot invalidate it, so
it prints a restart reminder on completion.
"""

import datetime
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from blocks.models import (
    Assessment,
    AuditLog,
    Block,
    Measurement,
    Officer,
    Quarry,
    WeeklyOfficerSummary,
)
from blocks import seigniorage
from blocks.config import DEFAULT_DENSITY_MT_PER_M3

# --- Provenance markers -------------------------------------------------------
DEMO_DEVICE_ID = "RTGS-DEMO-SEED-01"
DEMO_QUARRY_PREFIX = "APQ-"
DEMO_OFFICER_PREFIX = "APMD-"

# Anchor date for the dataset. Fixed rather than "now" so re-seeding produces
# the same relative spread and the trend charts stay reproducible.
ANCHOR = datetime.datetime(2026, 9, 21, 0, 0, 0)

# --- Quarries -----------------------------------------------------------------
# location MUST end in ", <lat>, <lon>" - MapDataAPIView parses the last two
# comma-separated fields as coordinates (views.py:1925-1932). Anything else and
# the quarry silently never appears on the map.
QUARRIES = [
    ("APQ-PKM-014", "Chimakurthy Black Galaxy Quarry Block-A",
     "Chimakurthy Mandal, Prakasam District, 15.5833, 79.9012",
     "Prakasam", "Coastal Andhra", "Sri Venkateswara Granites Pvt Ltd", 24.5, 2019, 2034),
    ("APQ-PKM-027", "Ballikurava Granite Quarry",
     "Ballikurava Mandal, Prakasam District, 15.9512, 80.0498",
     "Prakasam", "Coastal Andhra", "Kakatiya Stone Exports LLP", 18.2, 2020, 2035),
    ("APQ-NLR-008", "Podalakur Colour Granite Quarry",
     "Podalakur Mandal, SPSR Nellore District, 14.3671, 79.8158",
     "SPSR Nellore", "Coastal Andhra", "Penna Granite Industries", 31.7, 2018, 2033),
    ("APQ-ANM-005", "Madanapalle White Granite Quarry",
     "Madanapalle Mandal, Annamayya District, 13.5503, 78.5029",
     "Annamayya", "Rayalaseema", "Chittoor Rock Products Pvt Ltd", 15.9, 2021, 2036),
    ("APQ-SKL-011", "Burja Srikakulam Blue Granite Quarry",
     "Burja Mandal, Srikakulam District, 18.2949, 83.8938",
     "Srikakulam", "Uttarandhra", "Kalinga Granites Pvt Ltd", 22.4, 2019, 2034),
    ("APQ-VZM-003", "Gajapathinagaram River White Quarry",
     "Gajapathinagaram Mandal, Vizianagaram District, 18.1174, 83.4162",
     "Vizianagaram", "Uttarandhra", "Godavari Mineral Works", 12.8, 2022, 2037),
    ("APQ-PKM-039", "Ongole Rural Grey Granite Quarry",
     "Ongole Rural Mandal, Prakasam District, 15.5057, 80.0499",
     "Prakasam", "Coastal Andhra", "Sagar Stone Crushers and Granites", 9.6, 2023, 2038),
]

# Granite category per quarry, used to price that quarry's blocks through the
# official schedule. Each name is a key of seigniorage.CATEGORY_TO_GROUP.
QUARRY_CATEGORY = {
    "APQ-PKM-014": "Black Galaxy",
    "APQ-PKM-027": "Black Granite (Other)",
    "APQ-NLR-008": "Colour Granite",
    "APQ-ANM-005": "Madanapalli White",
    "APQ-SKL-011": "Srikakulam Blue",
    "APQ-VZM-003": "River White Vizag",
    "APQ-PKM-039": "Others",
}

# Quarry centre coordinates, for scattering block GPS nearby.
QUARRY_GPS = {
    "APQ-PKM-014": (15.5833, 79.9012),
    "APQ-PKM-027": (15.9512, 80.0498),
    "APQ-NLR-008": (14.3671, 79.8158),
    "APQ-ANM-005": (13.5503, 78.5029),
    "APQ-SKL-011": (18.2949, 83.8938),
    "APQ-VZM-003": (18.1174, 83.4162),
    "APQ-PKM-039": (15.5057, 80.0499),
}

# --- Officers -----------------------------------------------------------------
OFFICERS = [
    ("APMD-PKM-104", "K. Venkateswara Rao", "Assistant Director of Mines",
     ["APQ-PKM-014", "APQ-PKM-027"], "+91 98490 21447", "vrao.mines@apmines.example.in", 2017),
    ("APMD-PKM-118", "P. Ramakrishna Reddy", "Mining Inspector",
     ["APQ-PKM-039", "APQ-PKM-014"], "+91 94409 63318", "rkreddy.mines@apmines.example.in", 2019),
    ("APMD-NLR-062", "M. Sridevi", "Deputy Director of Mines",
     ["APQ-NLR-008"], "+91 99590 14472", "sridevi.m@apmines.example.in", 2015),
    ("APMD-ANM-047", "S. Lakshmi Prasanna", "Assistant Geologist",
     ["APQ-ANM-005"], "+91 96666 30285", "lprasanna.geo@apmines.example.in", 2021),
    ("APMD-SKL-029", "B. Naresh Kumar", "Mining Inspector",
     ["APQ-SKL-011"], "+91 91777 55190", "nkumar.mines@apmines.example.in", 2020),
    ("APMD-VZM-016", "T. Anitha Rani", "Assistant Director of Mines",
     ["APQ-VZM-003", "APQ-SKL-011"], "+91 93918 77204", "anitha.rani@apmines.example.in", 2018),
    ("APMD-PKM-133", "G. Srinivasulu", "Senior Mining Inspector",
     ["APQ-PKM-027", "APQ-NLR-008"], "+91 90005 41862", "srinivasulu.g@apmines.example.in", 2016),
]

# --- Blocks -------------------------------------------------------------------
# (block_id, quarry, officer, days_ago, hour, minute, L, B, H, confidence,
#  approval, has_gps, duration_s, attempts, lighting, reference_warnings,
#  wants_assessment)
#
# Dimensions are real granite dimension-stone sizes in metres. Blocks over
# 2.70 x 1.50 m classify Above Gangsaw under the official rule, so the mix
# deliberately straddles that boundary to exercise both rate bands.
BLOCKS = [
    ("APG-PKM-2026-0412", "APQ-PKM-014", "APMD-PKM-104", 41,  9, 15, 2.84, 1.62, 1.48, 0.94, "approved", True,  145, 1, "daylight", [], True),
    ("APG-PKM-2026-0418", "APQ-PKM-014", "APMD-PKM-104", 40, 11, 40, 3.05, 1.71, 1.55, 0.92, "approved", True,  162, 1, "daylight", [], True),
    ("APG-PKM-2026-0423", "APQ-PKM-027", "APMD-PKM-133", 39, 10,  5, 2.62, 1.44, 1.32, 0.89, "approved", True,  198, 2, "overcast", [], True),
    ("APG-NLR-2026-0207", "APQ-NLR-008", "APMD-NLR-062", 37,  8, 50, 2.95, 1.58, 1.41, 0.91, "approved", True,  176, 1, "daylight", [], True),
    ("APG-ANM-2026-0119", "APQ-ANM-005", "APMD-ANM-047", 36, 14, 20, 2.48, 1.36, 1.24, 0.88, "approved", True,  210, 1, "daylight", [], True),
    ("APG-SKL-2026-0331", "APQ-SKL-011", "APMD-SKL-029", 34,  9, 35, 3.18, 1.82, 1.63, 0.95, "approved", True,  154, 1, "daylight", [], True),
    ("APG-VZM-2026-0088", "APQ-VZM-003", "APMD-VZM-016", 33, 12, 10, 2.71, 1.52, 1.38, 0.90, "approved", True,  188, 1, "overcast", [], True),
    ("APG-PKM-2026-0431", "APQ-PKM-039", "APMD-PKM-118", 32, 15, 25, 2.34, 1.28, 1.19, 0.86, "approved", True,  223, 2, "daylight", [], True),
    ("APG-PKM-2026-0437", "APQ-PKM-014", "APMD-PKM-104", 30, 10, 45, 2.98, 1.68, 1.52, 0.93, "approved", True,  167, 1, "daylight", [], True),
    ("APG-NLR-2026-0214", "APQ-NLR-008", "APMD-PKM-133", 29, 11, 15, 2.76, 1.49, 1.35, 0.90, "approved", True,  181, 1, "daylight", [], True),
    ("APG-SKL-2026-0339", "APQ-SKL-011", "APMD-VZM-016", 28, 13, 30, 3.02, 1.74, 1.58, 0.94, "approved", True,  159, 1, "daylight", [], True),
    ("APG-ANM-2026-0126", "APQ-ANM-005", "APMD-ANM-047", 26,  9, 55, 2.55, 1.41, 1.29, 0.87, "rejected", True,  245, 3, "low_light", [], True),
    ("APG-PKM-2026-0444", "APQ-PKM-027", "APMD-PKM-133", 25, 10, 20, 2.88, 1.56, 1.44, 0.92, "approved", True,  172, 1, "daylight", [], True),
    ("APG-VZM-2026-0094", "APQ-VZM-003", "APMD-VZM-016", 24, 14, 45, 2.43, 1.33, 1.22, 0.85, "approved", True,  231, 2, "overcast", [], True),
    ("APG-PKM-2026-0452", "APQ-PKM-014", "APMD-PKM-118", 22,  8, 30, 3.11, 1.79, 1.61, 0.96, "approved", True,  148, 1, "daylight", [], True),
    ("APG-NLR-2026-0221", "APQ-NLR-008", "APMD-NLR-062", 21, 12,  5, 2.67, 1.46, 1.33, 0.89, "approved", True,  192, 1, "daylight", [], True),
    ("APG-SKL-2026-0347", "APQ-SKL-011", "APMD-SKL-029", 20, 10, 40, 2.92, 1.64, 1.49, 0.93, "approved", True,  165, 1, "daylight", [], True),
    ("APG-PKM-2026-0459", "APQ-PKM-039", "APMD-PKM-118", 18, 15, 10, 2.29, 1.24, 1.16, 0.84, "rejected", True,  258, 3, "low_light", [], True),
    ("APG-ANM-2026-0134", "APQ-ANM-005", "APMD-ANM-047", 17,  9, 25, 2.81, 1.54, 1.40, 0.91, "approved", True,  178, 1, "daylight", [], True),
    ("APG-VZM-2026-0101", "APQ-VZM-003", "APMD-VZM-016", 15, 11, 50, 2.58, 1.43, 1.31, 0.88, "approved", True,  203, 1, "overcast", [], True),
    ("APG-PKM-2026-0466", "APQ-PKM-027", "APMD-PKM-104", 13, 10, 15, 3.08, 1.76, 1.59, 0.95, "approved", True,  151, 1, "daylight", [], True),
    ("APG-NLR-2026-0228", "APQ-NLR-008", "APMD-PKM-133", 12, 13, 35, 2.72, 1.51, 1.37, 0.90, "approved", True,  185, 1, "daylight", [], True),
    ("APG-SKL-2026-0355", "APQ-SKL-011", "APMD-SKL-029", 11,  9, 45, 2.86, 1.61, 1.46, 0.92, "pending",  True,  169, 1, "daylight", [], True),
    ("APG-PKM-2026-0473", "APQ-PKM-014", "APMD-PKM-104",  9, 11, 20, 3.22, 1.85, 1.66, 0.97, "approved", True,  142, 1, "daylight", [], True),
    ("APG-ANM-2026-0141", "APQ-ANM-005", "APMD-ANM-047",  8, 14, 55, 2.51, 1.38, 1.27, 0.86, "pending",  True,  216, 2, "overcast", [], True),
    ("APG-VZM-2026-0108", "APQ-VZM-003", "APMD-VZM-016",  7, 10, 30, 2.64, 1.47, 1.34, 0.89, "pending",  True,  197, 1, "daylight", [], True),
    ("APG-PKM-2026-0477", "APQ-PKM-014", "APMD-PKM-104",  8, 10, 25, 3.01, 1.73, 1.57, 0.94, "approved", True,  156, 1, "daylight", [], True),
    ("APG-NLR-2026-0231", "APQ-NLR-008", "APMD-NLR-062",  6, 12, 40, 2.74, 1.50, 1.36, 0.90, "approved", True,  183, 1, "daylight", [], True),
    ("APG-SKL-2026-0359", "APQ-SKL-011", "APMD-VZM-016",  4,  9, 10, 2.89, 1.63, 1.47, 0.92, "approved", True,  171, 1, "daylight", [], True),
    ("APG-VZM-2026-0111", "APQ-VZM-003", "APMD-VZM-016",  3, 11, 55, 2.61, 1.45, 1.32, 0.88, "approved", True,  194, 1, "overcast", [], True),
    ("APG-PKM-2026-0492", "APQ-PKM-027", "APMD-PKM-133",  0,  7, 45, 2.93, 1.66, 1.50, 0.93, "approved", True,  164, 1, "daylight", [], True),
    ("APG-PKM-2026-0481", "APQ-PKM-039", "APMD-PKM-118",  2, 14,  5, 2.38, 1.31, 1.21, 0.85, "pending",  True,  238, 2, "daylight", [], False),
    ("APG-NLR-2026-0236", "APQ-NLR-008", "APMD-NLR-062",  1,  9, 30, 2.79, 1.53, 1.39, 0.91, "pending",  True,  174, 1, "daylight", [], False),
    ("APG-SKL-2026-0363", "APQ-SKL-011", "APMD-SKL-029",  1, 15, 45, 2.97, 1.67, 1.51, 0.93, "pending",  True,  161, 1, "daylight", [], False),
    ("APG-PKM-2026-0489", "APQ-PKM-027", "APMD-PKM-133",  0,  8, 15, 2.69, 1.48, 1.36, 0.72, "pending",  True,  268, 3, "low_light", [], False),
    ("APG-VZM-2026-0115", "APQ-VZM-003", "APMD-VZM-016",  0, 10,  5, 2.44, 1.34, 1.23, 0.68, "pending",  False, 291, 4, "low_light", ["gps_incomplete"], False),
    ("APG-PKM-2026-0495", "APQ-PKM-014", "APMD-PKM-118",  0, 12, 30, 3.14, 1.81, 1.64, 0.74, "pending",  True,  276, 3, "daylight", ["client_volume_mismatch"], False),
]

SUPERVISOR_ACTOR = "supervisor.rtgs"

# Weighbridge reconciliation, cycled deterministically across assessed blocks.
# variance_pct is the shortfall of assessed tonnage against the weighbridge
# reading - RevenueLeakageAPIView treats anything above 8.0% as leakage
# (views.py:1694). Most values sit inside normal weighing tolerance; a few do
# not, which is what makes the panel worth looking at.
WEIGHBRIDGE_VARIANCE = [
    0.8, 2.1, -1.4, 11.6, 0.3, 3.7, -0.9, 1.5, 9.2, 2.8,
    0.6, -2.2, 4.1, 1.9, 13.4, 0.4, 2.6, -1.1, 3.2, 8.7,
    1.2, 0.7, -0.5, 2.4, 5.3, 1.8, 0.9, 3.9, -1.7, 2.0, 1.1,
]


def _week_start(dt):
    """Monday 00:00 of the week containing dt - matches views.py bucketing."""
    monday = dt - datetime.timedelta(days=dt.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


class Command(BaseCommand):
    help = (
        "Seed a realistic demonstration dataset (quarries, officers, blocks, "
        "assessments, audit logs, weekly summaries) so every Supervisor "
        "Dashboard tab displays representative values. Never modifies or "
        "deletes the existing Blocks Registry."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--remove", action="store_true",
            help="Delete every demonstration record this command created, and nothing else.",
        )
        parser.add_argument(
            "--status", action="store_true",
            help="Report what is currently seeded, without writing anything.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Re-seed: remove existing demonstration records first, then seed again.",
        )

    # --- Registry protection --------------------------------------------------

    def _snapshot_existing(self):
        """Fingerprint every non-demo Block so any change to one is detectable."""
        snap = {}
        for b in Block.objects(device_id__ne=DEMO_DEVICE_ID):
            m = b.measurement
            snap[b.block_id] = (
                str(b.id), b.status, b.approval_status, b.inspecting_officer_id,
                b.submitted_quarry_id, b.gps_latitude, b.gps_longitude,
                (m.length_m, m.breadth_m, m.height_m, m.volume_m3) if m else None,
            )
        return snap

    def _assert_registry_intact(self, before):
        after = self._snapshot_existing()
        if before != after:
            missing = set(before) - set(after)
            added = set(after) - set(before)
            changed = {k for k in set(before) & set(after) if before[k] != after[k]}
            raise CommandError(
                "ABORTED - the existing Blocks Registry changed during this run. "
                f"missing={sorted(missing)} unexpected={sorted(added)} modified={sorted(changed)}"
            )

    # --- Demo record lookups --------------------------------------------------

    def _demo_blocks(self):
        return list(Block.objects(device_id=DEMO_DEVICE_ID))

    def _counts(self):
        demo_blocks = self._demo_blocks()
        return {
            "quarries": Quarry.objects(id__startswith=DEMO_QUARRY_PREFIX).count(),
            "officers": Officer.objects(officer_id__startswith=DEMO_OFFICER_PREFIX).count(),
            "blocks": len(demo_blocks),
            "assessments": Assessment.objects(block__in=demo_blocks).count() if demo_blocks else 0,
            "audit_logs": AuditLog.objects(block__in=demo_blocks).count() if demo_blocks else 0,
            "weekly_summaries": WeeklyOfficerSummary.objects(
                officer_id__startswith=DEMO_OFFICER_PREFIX).count(),
        }

    def handle(self, *args, **options):
        registry_before = self._snapshot_existing()
        self.stdout.write(f"Existing Blocks Registry: {len(registry_before)} records (read-only).")

        if options["status"]:
            for k, v in self._counts().items():
                self.stdout.write(f"  demo {k:18s}: {v}")
            return

        if options["remove"]:
            self._remove()
            self._assert_registry_intact(registry_before)
            self.stdout.write(self.style.SUCCESS("Demonstration records removed. Registry intact."))
            return

        existing = self._counts()
        if any(existing.values()):
            if not options["force"]:
                raise CommandError(
                    f"Demonstration data already present ({existing}). "
                    "Re-run with --force to replace it, or --remove to delete it."
                )
            self._remove()

        # Refuse to proceed if any demo block_id would collide with a real one.
        real_ids = set(registry_before)
        collisions = sorted(real_ids & {spec[0] for spec in BLOCKS})
        if collisions:
            raise CommandError(f"ABORTED - demo block IDs collide with real records: {collisions}")

        self._seed()
        self._assert_registry_intact(registry_before)

        final = self._counts()
        self.stdout.write(self.style.SUCCESS("\nDemonstration dataset created:"))
        for k, v in final.items():
            self.stdout.write(f"  {k:18s}: {v}")
        self.stdout.write(
            self.style.WARNING(
                "\nRestart the Django server before viewing the dashboard - analytics "
                "responses are cached for 300s per process and a management command "
                "cannot invalidate that cache."
            )
        )

    # --- Removal --------------------------------------------------------------

    def _remove(self):
        demo_blocks = self._demo_blocks()
        if demo_blocks:
            Assessment.objects(block__in=demo_blocks).delete()
            AuditLog.objects(block__in=demo_blocks).delete()
        WeeklyOfficerSummary.objects(officer_id__startswith=DEMO_OFFICER_PREFIX).delete()
        # Blocks before quarries: Quarry deletion NULLIFYs Block.quarry, which
        # would be a write to a Block document. Removing the demo blocks first
        # means there is nothing left to nullify.
        Block.objects(device_id=DEMO_DEVICE_ID).delete()
        Officer.objects(officer_id__startswith=DEMO_OFFICER_PREFIX).delete()
        Quarry.objects(id__startswith=DEMO_QUARRY_PREFIX).delete()

    # --- Seeding --------------------------------------------------------------

    def _seed(self):
        quarry_docs = {}
        for qid, name, location, district, region, lessee, area, reg_year, exp_year in QUARRIES:
            q = Quarry(
                id=qid, name=name, location=location, district=district, region=region,
                lessee_name=lessee, total_area_hectares=area,
                registered_date=datetime.datetime(reg_year, 4, 1),
                license_expiry_date=datetime.datetime(exp_year, 3, 31),
            )
            q.save(force_insert=True)
            quarry_docs[qid] = q
        self.stdout.write(f"  quarries         : {len(quarry_docs)}")

        for oid, name, desig, quarries, phone, email, joined in OFFICERS:
            Officer(
                officer_id=oid, name=name, designation=desig,
                assigned_quarries=[quarry_docs[q] for q in quarries],
                phone=phone, email=email,
                joined_date=datetime.datetime(joined, 6, 15), active_status=True,
            ).save(force_insert=True)
        self.stdout.write(f"  officers         : {len(OFFICERS)}")

        # Real granite photographs already on disk, so the dashboard's Field
        # Image panel renders an actual block rather than a broken link. No
        # image file is created, copied or modified.
        raw_dir = os.path.join(settings.MEDIA_ROOT, "raw")
        photos = sorted(p for p in os.listdir(raw_dir) if p.endswith(".jpg"))
        if not photos:
            raise CommandError(f"No block photographs found in {raw_dir}.")

        block_docs, n_assess, n_audit = [], 0, 0
        for i, spec in enumerate(BLOCKS):
            (bid, qid, oid, days_ago, hour, minute, L, B, H, conf,
             approval, has_gps, duration, attempts, lighting, warnings, wants_assess) = spec

            captured = ANCHOR - datetime.timedelta(days=days_ago)
            captured = captured.replace(hour=hour, minute=minute)

            lat = lon = None
            if has_gps:
                clat, clon = QUARRY_GPS[qid]
                # Deterministic scatter within roughly 400 m of the quarry centre.
                lat = round(clat + ((i * 37) % 71 - 35) * 0.00011, 6)
                lon = round(clon + ((i * 53) % 71 - 35) * 0.00011, 6)

            official = seigniorage.calculate(
                length_m=L, breadth_m=B, height_m=H,
                granite_category=QUARRY_CATEGORY[qid],
                density_mt_per_m3=DEFAULT_DENSITY_MT_PER_M3,
            )

            block = Block(
                block_id=bid,
                quarry=quarry_docs[qid],
                submitted_quarry_id=qid,
                inspecting_officer_id=oid,
                captured_at=captured,
                created_at=captured,
                updated_at=captured,
                gps_latitude=lat,
                gps_longitude=lon,
                status="measured",
                measurement=Measurement(
                    length_m=L, breadth_m=B, height_m=H,
                    volume_m3=official["volume_m3"],
                    confidence=conf,
                    measurement_method="ar",
                    measured_at=captured,
                ),
                cv_status="skipped",
                raw_image_path="raw/" + photos[i % len(photos)],
                approval_status=approval,
                approved_by=SUPERVISOR_ACTOR if approval in ("approved", "rejected") else None,
                approved_at=(captured + datetime.timedelta(days=1)) if approval in ("approved", "rejected") else None,
                reference_warnings=list(warnings),
                inspection_duration_seconds=duration,
                capture_attempt_count=attempts,
                lighting_condition=lighting,
                device_id=DEMO_DEVICE_ID,
            )
            block.save(force_insert=True)
            block_docs.append((block, spec, official))

            AuditLog(
                block=block, block_id_snapshot=bid, action="ar_measurement_recorded",
                actor=oid, timestamp=captured, severity="INFO",
                details=(
                    f"AR sizing recorded. L={L} B={B} H={H} "
                    f"V={official['volume_m3']} m3, confidence={conf}, "
                    f"duration={duration}s, attempts={attempts}."
                ),
            ).save()
            n_audit += 1

            if wants_assess:
                variance = WEIGHBRIDGE_VARIANCE[n_assess % len(WEIGHBRIDGE_VARIANCE)]
                weighbridge_mt = round(official["tonnage_mt"] * (1 + variance / 100.0), 3)
                Assessment(
                    block=block,
                    granite_category=official["granite_category"],
                    gangsaw_classification=official["gangsaw_classification"],
                    volume_m3=official["volume_m3"],
                    weight_mt=official["tonnage_mt"],
                    rate_per_mt=official["rate_per_mt"],
                    indicative_seigniorage=official["seigniorage_amount"],
                    density_mt_per_m3=official["density_mt_per_m3"],
                    weighbridge_weight_mt=weighbridge_mt,
                    variance_pct=variance,
                    expected_vs_actual_variance_pct=variance,
                    status="finalized" if approval == "approved" else "draft",
                    created_at=captured + datetime.timedelta(hours=6),
                    assessment_week=captured.isocalendar()[1],
                    assessment_month=captured.month,
                ).save()
                n_assess += 1

                AuditLog(
                    block=block, block_id_snapshot=bid, action="assessment_created",
                    actor=oid, timestamp=captured + datetime.timedelta(hours=6),
                    severity="INFO",
                    details=(
                        f"Seigniorage assessed under {official['rate_schedule_version']}. "
                        f"{official['granite_category']} / {official['gangsaw_classification']}, "
                        f"{official['tonnage_mt']} MT at INR {official['rate_per_mt']}/MT "
                        f"= INR {official['seigniorage_amount']}."
                    ),
                ).save()
                n_audit += 1

            if approval in ("approved", "rejected"):
                AuditLog(
                    block=block, block_id_snapshot=bid,
                    action=f"block_{approval}", actor=SUPERVISOR_ACTOR,
                    timestamp=captured + datetime.timedelta(days=1),
                    severity="INFO" if approval == "approved" else "WARNING",
                    details=(
                        f"Supervisor decision: {approval}. "
                        + ("Measurement and assessment verified."
                           if approval == "approved"
                           else "Returned for re-measurement - dimension confidence below threshold.")
                    ),
                ).save()
                n_audit += 1

        self.stdout.write(f"  blocks           : {len(block_docs)}")
        self.stdout.write(f"  assessments      : {n_assess}")
        self.stdout.write(f"  audit logs       : {n_audit}")

        self._seed_weekly(block_docs)

    def _seed_weekly(self, block_docs):
        """Weekly per-officer figures, computed from the blocks actually created.

        Nothing here is chosen: each week's count, confidence, approval rate and
        duration is the arithmetic of that officer's blocks in that week, so the
        Officer Weekly sparklines reconcile with Officers Analytics.
        """
        anchor_week = _week_start(ANCHOR)
        weeks = [anchor_week - datetime.timedelta(weeks=n) for n in range(5, -1, -1)]

        by_officer_week = {}
        for block, spec, _official in block_docs:
            key = (spec[2], _week_start(block.captured_at))
            by_officer_week.setdefault(key, []).append((block, spec))

        created = 0
        for oid, *_ in OFFICERS:
            for week in weeks:
                rows = by_officer_week.get((oid, week), [])
                n = len(rows)
                if n == 0:
                    WeeklyOfficerSummary(
                        officer_id=oid, week_start_date=week, blocks_inspected=0,
                        avg_confidence=0.0, override_count=0, approval_rate=0.0,
                        avg_inspection_duration_seconds=0.0,
                    ).save()
                    created += 1
                    continue
                WeeklyOfficerSummary(
                    officer_id=oid,
                    week_start_date=week,
                    blocks_inspected=n,
                    avg_confidence=round(sum(b.measurement.confidence for b, _ in rows) / n, 4),
                    override_count=0,
                    approval_rate=round(
                        sum(1 for b, _ in rows if b.approval_status == "approved") / n, 4),
                    avg_inspection_duration_seconds=round(
                        sum(b.inspection_duration_seconds for b, _ in rows) / n, 1),
                ).save()
                created += 1
        self.stdout.write(f"  weekly summaries : {created}")
