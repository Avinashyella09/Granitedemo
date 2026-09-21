"""
Create a Django auth user in the OFFICER or SUPERVISOR group and issue an API token.

Roles are plain auth Groups - no new user model. Nothing is generated: the
username, password and role all come from the operator.

    python manage.py create_user_role --username sup1 --role SUPERVISOR --password '...'
"""

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from rest_framework.authtoken.models import Token

from blocks.permissions import OFFICER_GROUP, SUPERVISOR_GROUP


class Command(BaseCommand):
    help = "Create an OFFICER or SUPERVISOR user and print its API token."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--password", required=True)
        parser.add_argument("--role", required=True, choices=[OFFICER_GROUP, SUPERVISOR_GROUP])

    def handle(self, *args, **options):
        username = options["username"].strip()
        if not username:
            raise CommandError("--username cannot be empty.")
        if User.objects.filter(username=username).exists():
            raise CommandError(
                f"User '{username}' already exists. This command never overwrites an existing user."
            )
        user = User.objects.create_user(username=username, password=options["password"])
        group, _ = Group.objects.get_or_create(name=options["role"])
        user.groups.add(group)
        token, _ = Token.objects.get_or_create(user=user)
        self.stdout.write(f"Created {options['role']} '{username}'")
        self.stdout.write(self.style.SUCCESS(f"API token: {token.key}"))
        self.stdout.write("Use header:  Authorization: Token <the token above>")
