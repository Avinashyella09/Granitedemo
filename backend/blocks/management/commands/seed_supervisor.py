"""
Seed / reset the PoC demo supervisor account.

    ./venv/bin/python manage.py seed_supervisor

IDEMPOTENT by design: run it any number of times and you end up with exactly
one 'rtgs_supervisor' user, active, in the SUPERVISOR group, with the demo
password set. It never creates a second user and never touches any other
account.

WHERE THE PASSWORD COMES FROM, and why it is not in this file
------------------------------------------------------------
The password is read from the RTGS_SUPERVISOR_PASSWORD environment variable,
which backend/settings.py loads from backend/.env - a file that is git-ignored.
Hardcoding it here would commit a working credential to the repository, which is
exactly what the project's security constraints forbid.

It is never printed: not to stdout, not to the Django log, and there is no API
that returns it. Django stores only a salted PBKDF2 hash via set_password().

Override for a one-off run with --password, or by exporting the variable.

This command seeds an ACCOUNT ONLY. It creates no Block, Quarry, Officer,
Assessment or AuditLog - nothing that could be mistaken for field data.
"""

import os

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError

from blocks.permissions import OFFICER_GROUP, SUPERVISOR_GROUP

DEMO_USERNAME = "rtgs_supervisor"
PASSWORD_ENV_VAR = "RTGS_SUPERVISOR_PASSWORD"


class Command(BaseCommand):
    help = "Create or reset the PoC demo supervisor account (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username", default=DEMO_USERNAME,
            help=f"Account to seed (default: {DEMO_USERNAME}).")
        parser.add_argument(
            "--password", default=None,
            help=f"Password to set. Defaults to ${PASSWORD_ENV_VAR} from the environment / .env.")

    def handle(self, *args, **options):
        username = options["username"].strip()
        password = options["password"] or os.environ.get(PASSWORD_ENV_VAR)

        if not password:
            raise CommandError(
                f"No password available. Set {PASSWORD_ENV_VAR} in backend/.env "
                f"(git-ignored) or pass --password. Refusing to seed an account "
                f"with a guessable default."
            )

        user, created = User.objects.get_or_create(username=username)

        # set_password hashes with PBKDF2 - the plaintext is never stored.
        user.set_password(password)
        user.is_active = True
        user.save()

        supervisor_group, _ = Group.objects.get_or_create(name=SUPERVISOR_GROUP)
        user.groups.add(supervisor_group)

        # A demo supervisor sitting in OFFICER as well would make the role
        # reported by /api/auth/me/ ambiguous. SUPERVISOR is what this account is
        # for, so any stray OFFICER membership is removed - only for this user.
        removed_officer = False
        officer_group = Group.objects.filter(name=OFFICER_GROUP).first()
        if officer_group and user.groups.filter(pk=officer_group.pk).exists():
            user.groups.remove(officer_group)
            removed_officer = True

        groups = sorted(user.groups.values_list("name", flat=True))
        total_matching = User.objects.filter(username=username).count()

        # Deliberately reports WHAT happened without ever echoing the password.
        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} supervisor account '{username}'."))
        self.stdout.write(f"  active        : {user.is_active}")
        self.stdout.write(f"  groups        : {', '.join(groups) or '(none)'}")
        if removed_officer:
            self.stdout.write(f"  removed       : {OFFICER_GROUP} membership (conflicting role)")
        self.stdout.write(f"  password      : set from ${PASSWORD_ENV_VAR} (not displayed)")
        self.stdout.write(f"  users matching: {total_matching} (must be 1)")
        self.stdout.write(
            "  No Block, Quarry, Officer, Assessment or AuditLog was created.")
