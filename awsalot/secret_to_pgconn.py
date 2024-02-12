from typing import List
import json
import sys

from blessed import Terminal
import boto3
import click
import inquirer, inquirer.render.console


class StdErrRenderer(inquirer.render.ConsoleRender):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.terminal = Terminal(stream=sys.stderr)

    def render(self, question, answers=None):
        question.answers = answers or {}

        if question.ignore:
            return question.default

        clazz = self.render_factory(question.kind)
        render = clazz(
            question,
            terminal=self.terminal,
            theme=self._theme,
            show_default=question.show_default,
        )

        self.clear_eos()

        try:
            return self._event_loop(render)
        finally:
            print("", file=self.terminal.stream)

    def _relocate(self):
        print(self._position * self.terminal.move_up, end="", file=self.terminal.stream)
        self._force_initial_column()
        self._position = 0

    def _go_to_end(self, render):
        positions = len(list(render.get_options())) - self._position
        if positions > 0:
            print(
                self._position * self.terminal.move_down,
                end="",
                file=self.terminal.stream,
            )
        self._position = 0

    def print_str(self, base, lf=False, **kwargs):
        if lf:
            self._position += 1

        print(
            base.format(t=self.terminal, **kwargs),
            end="\n" if lf else "",
            flush=True,
            file=sys.stderr,
        )

    def clear_eos(self):
        print(self.terminal.clear_eos(), end="", file=self.terminal.stream)


def fetch_aws_secrets(filters: List[str] = []):
    """Fetches a list of secret names from AWS Secrets Manager."""
    client = boto3.client("secretsmanager")

    paginator = client.get_paginator("list_secrets")
    for page in paginator.paginate():
        for secret in page["SecretList"]:
            name = secret["Name"]
            if filters and not any(filter in name.lower() for filter in filters):
                continue
            yield name


def get_secret_value(secret_name):
    """Retrieves the value of the selected secret from AWS Secrets Manager."""
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_name)
    secret = response["SecretString"]
    return json.loads(secret)


def format_postgres_connection_string(secret_details):
    """Formats the secret details into a Postgres connection string."""
    host = secret_details["host"]
    user = secret_details["username"]
    password = secret_details["password"]
    port = secret_details["port"]
    dbname = secret_details.get("dbname")
    return (
        f"postgresql://{user}:{password}@{host}:{port}{'/' + dbname if dbname else ''}"
    )


@click.command()
@click.option("--filter", "-f", multiple=True)
def main(filter):
    """
    Generate a Postgresql connection string from an AWS SecretsManager Secret.
    """
    print("Fetching AWS Secrets...", file=sys.stderr)
    secrets = fetch_aws_secrets(filters=filter)
    questions = [
        inquirer.List(
            "secret",
            message="Select an AWS Secret containing Postgres connection info",
            choices=[secret for secret in secrets],
        ),
    ]
    answers = inquirer.prompt(questions, render=StdErrRenderer())
    secret_details = get_secret_value(answers["secret"])

    if not secret_details:
        print("Failed to retrieve secret details.", file=sys.stderr)
        return

    connection_string = format_postgres_connection_string(secret_details)
    print(f"Your Postgres connection string is: ", file=sys.stderr)
    print(connection_string, end="")
