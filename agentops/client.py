"""
AgentOps client module that provides a client class with public interfaces and configuration.

Classes:
    Client: Provides methods to interact with the AgentOps service.
"""
from .agent import Agent
from .event import Event
from .helpers import get_ISO_time
from .session import Session
from .worker import Worker
from uuid import uuid4
from typing import Optional, List
from pydantic import Field
from os import environ
import functools
import traceback
import logging
import inspect
import atexit
import signal
import sys

from .config import Configuration
from .llm_tracker import LlmTracker


class Client:
    """
    Client for AgentOps service.

    Args:
        api_key (str, optional): API Key for AgentOps services. If none is provided, key will be read from the AGENTOPS_API_KEY environment variable.
        tags (List[str], optional): Tags for the sessions that can be used for grouping or sorting later (e.g. ["GPT-4"]).
        endpoint (str, optional): The endpoint for the AgentOps service. Defaults to 'https://agentops-server-v2.fly.dev'.
        max_wait_time (int, optional): The maximum time to wait in milliseconds before flushing the queue. Defaults to 1000.
        max_queue_size (int, optional): The maximum size of the event queue. Defaults to 100.
        override (bool): Whether to override and LLM calls to emit as events.
        use_named_agents (bool): To use the AgentOps Agent class for agent specific reporting, this must be True.
        auto_start_session (bool): Client does not create an AgentOps session on init. Must be manually created later.
    Attributes:
        _session (Session, optional): A Session is a grouping of events (e.g. a run of your agent).
    """

    def __init__(self, api_key: Optional[str] = None,
                 tags: Optional[List[str]] = None,
                 endpoint: Optional[str] = 'https://api.agentops.ai',
                 max_wait_time: Optional[int] = 1000,
                 max_queue_size: Optional[int] = 100,
                 override=True,
                 auto_start_session=True,
                 use_named_agents=False
                 ):

        self._session = None
        self._worker = None
        self._tags = tags
        self.config = None
        self._uses_named_agents = use_named_agents

        if not api_key and not environ.get('AGENTOPS_API_KEY'):
            return logging.warn("AgentOps: No API key provided - no data will be recorded.")

        self.config = Configuration(api_key or environ.get('AGENTOPS_API_KEY'),
                                    endpoint,
                                    max_wait_time,
                                    max_queue_size)

        self._handle_unclean_exits()

        if auto_start_session:
            self.start_session(tags)

        if override and not use_named_agents:
            if 'openai' in sys.modules:
                self.llm_tracker = LlmTracker(self)
                self.llm_tracker.override_api('openai')

    def add_tags(self, tags: List[str]):
        if self._session is None:
            return print("You must create a session before assigning tags")

        if self._tags is not None:
            self._tags.extend(tags)
        else:
            self._tags = tags

        self._session.tags = self._tags
        self._worker.update_session(self._session)

    def set_tags(self, tags: List[str]):
        if self._session is None:
            return print("You must create a session before assigning tags")

        self._tags = tags
        self._session.tags = tags
        self._worker.update_session(self._session)

    def record(self, event: Event):
        """
        Record an event with the AgentOps service.

        Args:
            event (Event): The event to record.
        """

        if self._session is not None and not self._session.has_ended:
            self._worker.add_event(
                {'session_id': self._session.session_id, **event.__dict__})
        else:
            logging.warn("AgentOps: Cannot record event - no current session")

    def record_function(self, event_name: str, tags: Optional[List[str]] = None):
        """
        Decorator to record an event before and after a function call.
        Usage:
            - Actions: Records function parameters and return statements of the
                function being decorated. Additionally, timing information about
                the action is recorded
        Args:
            event_name (str): The name of the event to record.
            tags (List[str], optional): Any tags associated with the event. Defaults to None.
        """

        def decorator(func):
            if inspect.iscoroutinefunction(func):
                @functools.wraps(func)
                async def async_wrapper(*args, **kwargs):
                    return await self._record_event_async(func, event_name, tags, *args, **kwargs)
                return async_wrapper
            else:
                @functools.wraps(func)
                def sync_wrapper(*args, **kwargs):
                    return self._record_event_sync(func, event_name, tags, *args, **kwargs)
                return sync_wrapper

        return decorator

    def _record_event_sync(self, func, event_name, tags, *args, **kwargs):
        init_time = get_ISO_time()
        func_args = inspect.signature(func).parameters
        arg_names = list(func_args.keys())
        # Get default values
        arg_values = {name: func_args[name].default
                      for name in arg_names if func_args[name].default
                      is not inspect._empty}
        # Update with positional arguments
        arg_values.update(dict(zip(arg_names, args)))
        arg_values.update(kwargs)

        try:
            returns = func(*args, **kwargs)

            # If the function returns multiple values, record them all in the same event
            if isinstance(returns, tuple):
                returns = list(returns)

            # Record the event after the function call
            self.record(Event(event_type=event_name,
                              params=arg_values,
                              returns=returns,
                              result='Success',
                              action_type='action',
                              init_timestamp=init_time,
                              tags=tags))

        except Exception as e:
            # Record the event after the function call
            self.record(Event(event_type=event_name,
                              params=arg_values,
                              returns={f"{type(e).__name__}": str(e)},
                              result='Fail',
                              action_type='action',
                              init_timestamp=init_time,
                              tags=tags))

            # Re-raise the exception
            raise

        return returns

    async def _record_event_async(self, func, event_name, tags, *args, **kwargs):
        init_time = get_ISO_time()
        func_args = inspect.signature(func).parameters
        arg_names = list(func_args.keys())
        # Get default values
        arg_values = {name: func_args[name].default
                      for name in arg_names if func_args[name].default
                      is not inspect._empty}
        # Update with positional arguments
        arg_values.update(dict(zip(arg_names, args)))
        arg_values.update(kwargs)

        try:

            returns = await func(*args, **kwargs)

            # If the function returns multiple values, record them all in the same event
            if isinstance(returns, tuple):
                returns = list(returns)

            # Record the event after the function call
            self.record(Event(event_type=event_name,
                              params=arg_values,
                              returns=returns,
                              result='Success',
                              action_type='action',
                              init_timestamp=init_time,
                              tags=tags))

        except Exception as e:
            # Record the event after the function call
            self.record(Event(event_type=event_name,
                              params=arg_values,
                              returns={f"{type(e).__name__}": str(e)},
                              result='Fail',
                              action_type='action',
                              init_timestamp=init_time,
                              tags=tags))

            # Re-raise the exception
            raise

        return returns

    # TODO: allow the developer to select which LLM provider
    def create_agent(self, name: str) -> Agent:
        if not self._uses_named_agents:
            raise Exception("To use named agents, the AgentOps client must be initialized with the optional "
                            "parameter: uses_named_agents=True")
        agent = Agent(self.config, self, name, self._session.session_id)
        return agent

    def start_session(self, tags: Optional[List[str]] = None, config: Optional[Configuration] = None):
        """
        Start a new session for recording events.

        Args:
            tags (List[str], optional): Tags that can be used for grouping or sorting later.
                e.g. ["test_run"].
        """
        if self._session is not None:
            return logging.warn("AgentOps: Cannot start session - session already started")

        if not config and not self.config:
            return logging.warn("AgentOps: Cannot start session - missing configuration")

        self._session = Session(str(uuid4()), tags or self._tags)
        self._worker = Worker(config or self.config)
        self._worker.start_session(self._session)

    def end_session(self, end_state: str = Field("Indeterminate",
                                                 description="End state of the session",
                                                 pattern="^(Success|Fail|Indeterminate)$"),
                    rating: Optional[str] = None,
                    end_state_reason: Optional[str] = None,
                    video: Optional[str] = None):
        """
        End the current session with the AgentOps service.

        Args:
            end_state (str, optional): The final state of the session.
            rating (str, optional): The rating for the session.
            end_state_reason (str, optional): The reason for ending the session.
            video (str, optional): The video screen recording of the session
        """
        if self._session is None or self._session.has_ended:
            return logging.warn("AgentOps: Cannot end session - no current session")

        self._session.video = video
        self._session.end_session(end_state, rating, end_state_reason)
        self._worker.end_session(self._session)
        self._session = None
        self._worker = None

    def _handle_unclean_exits(self):
        def cleanup(end_state_reason: Optional[str] = None):
            # Only run cleanup function if session is created
            if self._session is not None:
                self.end_session(end_state='Fail',
                                 end_state_reason=end_state_reason)

        def signal_handler(signum, frame):
            """
            Signal handler for SIGINT (Ctrl+C) and SIGTERM. Ends the session and exits the program.

            Args:
                signum (int): The signal number.
                frame: The current stack frame.
            """
            signal_name = 'SIGINT' if signum == signal.SIGINT else 'SIGTERM'
            logging.info(f'Signal {signal_name} detected. Ending session...')
            self.end_session(end_state='Fail',
                             end_state_reason=f'Signal {signal_name} detected')
            sys.exit(0)

        def handle_exception(exc_type, exc_value, exc_traceback):
            """
            Handle uncaught exceptions before they result in program termination.

            Args:
                exc_type (Type[BaseException]): The type of the exception.
                exc_value (BaseException): The exception instance.
                exc_traceback (TracebackType): A traceback object encapsulating the call stack at the point where the exception originally occurred.
            """
            formatted_traceback = ''.join(traceback.format_exception(exc_type, exc_value,
                                                                     exc_traceback))

            # Perform cleanup
            cleanup(
                end_state_reason=f"{str(exc_value)}: {formatted_traceback}")

            # Then call the default excepthook to exit the program
            sys.__excepthook__(exc_type, exc_value, exc_traceback)

        atexit.register(lambda: cleanup())
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        sys.excepthook = handle_exception
