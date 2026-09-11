"""Credential-free demonstration commands."""

from pathlib import Path
from tempfile import TemporaryDirectory

import typer

from jobs_status_manager.application_core.domain import (
    ApplicationStatus,
    StatusUpdateArguments,
    UserId,
)
from jobs_status_manager.application_core.service import (
    execute_status_update,
    propose_status_update,
    resolve_confirmation,
)
from jobs_status_manager.bootstrap.cli_support import load_settings, project_root
from jobs_status_manager.bootstrap.service import bootstrap_identity
from jobs_status_manager.config.settings import AppSettings
from jobs_status_manager.event_pipeline import EventServices, publish_once
from jobs_status_manager.infrastructure.adapters.fakes import (
    FakeIMAPGateway,
    FakeLLM,
    FakeQQGateway,
)
from jobs_status_manager.infrastructure.clock import SystemClock
from jobs_status_manager.infrastructure.database.connection import Database
from jobs_status_manager.infrastructure.database.migrations import upgrade_database
from jobs_status_manager.infrastructure.ids import UUIDGenerator
from jobs_status_manager.mail import (
    AnalysisConfidence,
    AnalysisDetails,
    ApplicationSuggestion,
    JobMailAnalysisInput,
    MailEnvelope,
    MailType,
    StatusSuggestion,
)
from jobs_status_manager.mail_poller import poll_once
from jobs_status_manager.mail_service import MailServices
from jobs_status_manager.notifications import NotificationServices, dispatch_once


def register(app: typer.Typer) -> None:
    """Register credential-free local demos."""

    @app.command("demo-status-core")
    def demo_status_core() -> None:
        """Run the local Phase 1 proposal, confirmation, and execution loop."""
        settings = load_settings()
        root = project_root()
        database_url = f"sqlite:///{settings.database_path}"
        upgrade_database(root, database_url)
        database = Database(settings.database_path)
        try:
            identity = bootstrap_identity(database, settings, SystemClock(), UUIDGenerator())
            clock = SystemClock()
            ids = UUIDGenerator()
            arguments = StatusUpdateArguments(
                company="腾讯",
                department=None,
                position="后端开发",
                status=ApplicationStatus.INTERVIEW,
                interview_round=1,
            )
            action = propose_status_update(
                database,
                user_id=UserId(identity.user_id),
                source_id="demo-status-core",
                arguments=arguments,
                clock=clock,
                ids=ids,
            )
            typer.echo(f"proposal state={action.state} code={action.confirmation_code}")
            result = resolve_confirmation(
                database,
                user_id=UserId(identity.user_id),
                command_text=f"确认 {action.confirmation_code}",
                clock=clock,
                ids=ids,
            )
            if result is None:
                message = "demo confirmation was not routed"
                raise RuntimeError(message)
            execution = execute_status_update(database, action_id=action.id, clock=clock, ids=ids)
            typer.echo(
                f"confirmation={result.state} execution={execution.state} "
                f"application={execution.application_id} changed={execution.changed}"
            )
        finally:
            database.dispose()

    @app.command("demo-mail-pipeline")
    def demo_mail_pipeline() -> None:
        """Run the complete credential-free Phase 2 fake mail pipeline."""
        root = project_root()
        with TemporaryDirectory(prefix="jobs-mail-demo-") as temporary:
            data_dir = Path(temporary)
            settings = AppSettings(
                database_path=data_dir / "jobs.db",
                data_dir=data_dir,
                bootstrap_user_external_key="demo-user",
                bootstrap_user_display_name="Demo User",
                bootstrap_mail_provider="fake",
                bootstrap_mail_account_key="demo-mail",
                bootstrap_mail_display_name="Demo Mail",
                qq_user_openid="openid-demo",
            )
            upgrade_database(root, f"sqlite:///{settings.database_path}")
            database = Database(settings.database_path)
            clock = SystemClock()
            ids = UUIDGenerator()
            identity = bootstrap_identity(database, settings, clock, ids)
            analysis = JobMailAnalysisInput(
                application=ApplicationSuggestion(
                    company="腾讯", department=None, position="后端开发"
                ),
                mail_type=MailType.INTERVIEW,
                status_suggestion=StatusSuggestion(
                    status=ApplicationStatus.INTERVIEW, should_update=True, interview_round=2
                ),
                details=AnalysisDetails(),
                summary="腾讯二面邀请",
                confidence=AnalysisConfidence(application_match=0.99, status_suggestion=0.99),
            )
            envelope = MailEnvelope(
                provider_message_id="demo-mail-1",
                subject="腾讯二面邀请",
                sender="hr@example.com",
                recipients=("user@example.com",),
                received_at=clock.now(),
                content="请参加二面。",
            )
            mail_services = MailServices(database, clock, ids)
            poll_once(mail_services, identity.mail_account_id, FakeIMAPGateway(messages=[envelope]))
            event_services = EventServices(mail_services, FakeLLM(analysis=analysis))
            publish_once(event_services)
            publish_once(event_services)
            publish_once(event_services)
            qq = FakeQQGateway()
            dispatch_once(NotificationServices(database, clock, ids), qq)
            typer.echo(f"mail_pipeline pushes={len(qq.calls)} application_changes=0")
            database.dispose()
