
from dataclasses import dataclass

MAX_STABILIZATION_MS = 1200

@dataclass
class StreamEvent:
    sequence: int
    text: str
    final: bool = False

class CaptionMux:
    def __init__(self):
        self.last_sequence = -1
        self.visible = ""

    def ingest(self, event: StreamEvent) -> str:
        # Provider reconnects may replay a prefix. This implementation
        # appends every accepted payload and therefore duplicates captions.
        if event.sequence < self.last_sequence:
            return ""
        self.last_sequence = event.sequence
        self.visible += event.text
        return event.text
