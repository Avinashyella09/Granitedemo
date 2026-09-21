"""
Create ONE real Officer record from values the operator supplies explicitly.

Same contract as create_quarry: nothing is generated, nothing is overwritten.

    python manage.py create_officer \
        --officer-id OFF-41 \
        --name "A. Kumar" \
        --designation "Mining Inspector" \
        --assigned-quarry Q-9982 \
        --dry-run

--assigned-quarry may be repeated. Every referenced quarry must already exist:
Officer.assigned_quarries is a ReferenceField list, so a dangling reference
would produce an Officer pointing at nothing, which is worse than a refusal.
"""

from django.core.management.base import BaseCommand, CommandError

from blocks.models import Officer, Quarry

from . import _refdata


class Command(BaseCommand):
    help = "Create a single Officer from explicitly supplied values. Never generates data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--officer-id", dest="officer_id", required=True, help="Officer ID, e.g. OFF-41"
        )
        parser.add_argument("--name", required=True, help="Officer name")
        parser.add_argument("--designation", help="Designation")
        parser.add_argument(
            "--assigned-quarry",
            dest="assigned_quarries",
            action="append",
            help="Quarry ID to assign. Repeat for several. Each must already exist.",
        )
        parser.add_argument("--phone", help="Phone number")
        parser.add_argument("--email", help="Email address")
        parser.add_argument("--joined-date", dest="joined_date", help="YYYY-MM-DD")
        parser.add_argument(
            "--active-status",
            dest="active_status",
            help="true or false. Defaults to the model default (true) when omitted.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and show what would be created. Performs zero database writes.",
        )

    def handle(self, *args, **options):
        officer_id = _refdata.required_text(options["officer_id"], "officer_id", 100)
        name = _refdata.required_text(options["name"], "name", 255)

        designation = _refdata.optional_text(options.get("designation"), "designation", 100)
        phone = _refdata.optional_text(options.get("phone"), "phone", 50)
        email = _refdata.optional_email(options.get("email"), "email")
        joined_date = _refdata.optional_date(options.get("joined_date"), "joined_date")
        active_status = _refdata.parse_bool(options.get("active_status"), "active_status")

        if Officer.objects(officer_id=officer_id).first() is not None:
            raise CommandError(
                f"Officer '{officer_id}' already exists. "
                f"This command never overwrites an existing record - "
                f"choose a different --officer-id, or change the stored record deliberately by other means."
            )

        # Resolve every assigned quarry before writing anything, and report all
        # unknown ids at once rather than failing on the first.
        requested = options.get("assigned_quarries") or []
        resolved, unknown, seen = [], [], set()
        for raw in requested:
            qid = _refdata.required_text(raw, "assigned_quarry", 100)
            if qid in seen:
                continue
            seen.add(qid)
            quarry = Quarry.objects(id=qid).first()
            if quarry is None:
                unknown.append(qid)
            else:
                resolved.append(quarry)

        if unknown:
            raise CommandError(
                "Unknown quarry ID(s): "
                + ", ".join(repr(q) for q in unknown)
                + ". Create the quarry first with `manage.py create_quarry`. "
                "No officer was created."
            )

        summary = {
            "officer_id": officer_id,
            "name": name,
            "designation": designation,
            "assigned_quarries": ", ".join(q.id for q in resolved) if resolved else None,
            "phone": phone,
            "email": email,
            "joined_date": joined_date.date() if joined_date else None,
            "active_status": active_status if active_status is not None else "(model default: True)",
        }

        if options["dry_run"]:
            self.stdout.write(_refdata.render("DRY RUN - would create Officer:", summary))
            self.stdout.write(self.style.WARNING("No database write performed."))
            return

        officer = Officer(officer_id=officer_id, name=name)
        if designation is not None:
            officer.designation = designation
        if resolved:
            officer.assigned_quarries = resolved
        if phone is not None:
            officer.phone = phone
        if email is not None:
            officer.email = email
        if joined_date is not None:
            officer.joined_date = joined_date
        if active_status is not None:
            officer.active_status = active_status
        officer.save(force_insert=True)

        self.stdout.write(_refdata.render("Created Officer:", summary))
        self.stdout.write(self.style.SUCCESS(f"Officer '{officer_id}' created."))
