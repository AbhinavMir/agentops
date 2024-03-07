# agentops/__init__.py

from .client import Client
from .event import ActionEvent, ErrorEvent, LLMEvent, ToolEvent
from .logger import AgentOpsLogger
from .enums import Models

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
from opentelemetry.instrumentation.instrumentor import BaseInstrumentor


class MySDKInstrumentor(BaseInstrumentor):
    def _instrument(self, **kwargs):
        """Instrument the SDK to enable tracing and export to console, respecting existing configurations."""

        # Check if a TracerProvider is already configured
        if not trace.get_tracer_provider()._is_default():  # Check for the default TracerProvider # TODO
            # A TracerProvider is already configured, log or handle accordingly
            print("A TracerProvider is already configured. MySDK will use the existing TracerProvider.")
        else:
            # Set up the tracer provider
            trace.set_tracer_provider(TracerProvider())

            # Configure the console exporter to print traces to stdout
            console_exporter = ConsoleSpanExporter()

            # Add the console exporter to the tracer provider
            trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(console_exporter))

    def _uninstrument(self, **kwargs):
        """Remove instrumentation from the SDK (if necessary)."""
        # Implementation to revert any instrumentation changes


def some_function_in_your_sdk():
    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span("operation_name"):
        # Your SDK operation logic here
        pass


# Ensure the SDK is instrumented
MySDKInstrumentor().instrument()
