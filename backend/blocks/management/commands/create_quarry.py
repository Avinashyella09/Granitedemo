"""
Create ONE real Quarry record from values the operator supplies explicitly.

This is deliberately not a seeder. Every value comes from the command line;
nothing is generated, defaulted to a plausible-looking string, or inferred.
Contrast generate_mock_data, which fabricates everything with random.* - a
record from that command is demo data, a record from this one is real.

    python manage.py create_quarry \
        --id Q-9982 \
        --name "AP Mines - Chimakurthy Main Quarry" \
        --district Prakasam \
        --dry-run

Re-running with an existing --id is a conflict, not an update: the command
refuses and leaves the stored record untouched. There is no update or delete
mode here by design.
"""

from django.core.management.base import BaseCommand, CommandError

from blocks.models import Quarry

from . import _refdata


class Command(BaseCommand):
    help = "Create a single Quarry from explicitly supplied values. Never generates data."

    def add_arguments(self, parser):
        parser.add_argument("--id", required=True, help="Quarry ID (primary key), e.g. Q-9982")
        parser.add_argument("--name", required=True, help="Quarry name")
        parser.add_argument("--location", help="Free-text location / survey details")
        parser.add_argument("--district", help="District")
        parser.add_argument("--region", help="Region")
        parser.add_argument("--lessee-name", dest="lessee_name", help="Lessee name")
        parser.add_argument(
            "--total-area-hectares",
            dest="total_area_hectares",
            help="Total area in hectares (positive number)",
        )
        parser.add_argument(
            "--registered-date", dest="registered_date", help="YYYY-MM-DD"
        )
        parser.add_argument(
            "--license-expiry-date", dest="license_expiry_date", help="YYYY-MM-DD"
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and show what would be created. Performs zero database writes.",
        )

    def handle(self, *args, **options):
        quarry_id = _refdata.required_text(options["id"], "id", 100)
        name = _refdata.required_text(options["name"], "name", 255)

        location = _refdata.optional_text(options.get("location"), "location", 500)
        district = _refdata.optional_text(options.get("district"), "district", 100)
        region = _refdata.optional_text(options.get("region"), "region", 100)
        lessee_name = _refdata.optional_text(options.get("lessee_name"), "lessee_name", 255)
        total_area = _refdata.optional_positive_float(
            options.get("total_area_hectares"), "total_area_hectares"
        )
        registered_date = _refdata.optional_date(options.get("registered_date"), "registered_date")
        license_expiry_date = _refdata.optional_date(
            options.get("license_expiry_date"), "license_expiry_date"
        )

        if registered_date and license_expiry_date and license_expiry_date < registered_date:
            raise CommandError(
                "--license-expiry-date is earlier than --registered-date "
                f"({license_expiry_date.date()} < {registered_date.date()})."
            )

        # Uniqueness is checked before any write. Quarry.id is the primary key,
        # so saving over an existing one would silently replace a real record.
        if Quarry.objects(id=quarry_id).first() is not None:
            raise CommandError(
                f"Quarry '{quarry_id}' already exists. "
                f"This command never overwrites an existing record - "
                f"choose a different --id, or change the stored record deliberately by other means."
            )

        summary = {
            "id": quarry_id,
            "name": name,
            "location": location,
            "district": district,
            "region": region,
            "lessee_name": lessee_name,
            "total_area_hectares": total_area,
            "registered_date": registered_date.date() if registered_date else None,
            "license_expiry_date": license_expiry_date.date() if license_expiry_date else None,
        }

        if options["dry_run"]:
            self.stdout.write(_refdata.render("DRY RUN - would create Quarry:", summary))
            self.stdout.write(self.style.WARNING("No database write performed."))
            return

        quarry = Quarry(id=quarry_id, name=name)
        if location is not None:
            quarry.location = location
        if district is not None:
            quarry.district = district
        if region is not None:
            quarry.region = region
        if lessee_name is not None:
            quarry.lessee_name = lessee_name
        if total_area is not None:
            quarry.total_area_hectares = total_area
        if registered_date is not None:
            quarry.registered_date = registered_date
        if license_expiry_date is not None:
            quarry.license_expiry_date = license_expiry_date
        quarry.save(force_insert=True)

        self.stdout.write(_refdata.render("Created Quarry:", summary))
        self.stdout.write(self.style.SUCCESS(f"Quarry '{quarry_id}' created."))
