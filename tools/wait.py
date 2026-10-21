import asyncio
import os
from datetime import timedelta
from helpers.tool import Tool, Response
from helpers.print_style import PrintStyle
from helpers.wait import managed_wait
from helpers.localization import Localization

# Longest wait one call may hold a run for. A wait holds the conversation's run and its thread; anything later
# belongs to the scheduler.
MAX_WAIT_SECONDS = int(os.environ.get("REZZTOR_MAX_WAIT_SECONDS", str(24 * 3600)))


def _too_long() -> str:
    return (
        f"A wait may last at most {MAX_WAIT_SECONDS} seconds. "
        "For anything later, schedule a task instead of waiting."
    )


class WaitTool(Tool):

    async def execute(self, **kwargs) -> Response:
        await self.agent.handle_intervention()

        seconds = self.args.get("seconds", 0)
        minutes = self.args.get("minutes", 0)
        hours = self.args.get("hours", 0)
        days = self.args.get("days", 0)
        until_timestamp_str = self.args.get("until")

        is_duration_wait = not bool(until_timestamp_str)

        now = Localization.get().now()
        target_time = None

        if until_timestamp_str:
            try:
                target_time = Localization.get().localtime_str_to_utc_dt(until_timestamp_str)
                if not target_time:
                    raise ValueError(f"Invalid timestamp format: {until_timestamp_str}")
            except ValueError as e:
                return Response(
                    message=str(e),
                    break_loop=False,
                )
        else:
            total = int(days) * 86400 + int(hours) * 3600 + int(minutes) * 60 + int(seconds)
            if total > MAX_WAIT_SECONDS:
                return Response(message=_too_long(), break_loop=False)
            wait_duration = timedelta(seconds=total)
            if wait_duration.total_seconds() <= 0:
                return Response(
                    message="Wait duration must be positive.",
                    break_loop=False,
                )
            target_time = now + wait_duration
        
        if (target_time - now).total_seconds() > MAX_WAIT_SECONDS:
            return Response(message=_too_long(), break_loop=False)

        if target_time <= now:
            return Response(
                message=f"Target time {Localization.get().serialize_datetime(target_time)} is in the past.",
                break_loop=False,
            )

        PrintStyle.info(f"Waiting until {Localization.get().serialize_datetime(target_time)}...")

        target_time = await managed_wait(
            agent=self.agent,
            target_time=target_time,
            is_duration_wait=is_duration_wait,
            log=self.log,
            get_heading_callback=self.get_heading
        )

        if self.log:
            self.log.update(heading=self.get_heading("Done", done=True))

        message = self.agent.read_prompt(
            "fw.wait_complete.md",
            target_time=Localization.get().serialize_datetime(target_time)
        )

        return Response(
            message=message,
            break_loop=False,
        )

    def get_log_object(self):
        return self.agent.context.log.log(
            type="progress",
            heading=self.get_heading(),
            content="",
            kvps=self.args,
        )

    def get_heading(self, text: str = "", done: bool = False):
        done_icon = " icon://done_all" if done else ""
        if not text:
            text = f"Waiting..."
        return f"icon://timer Wait: {text}{done_icon}"
