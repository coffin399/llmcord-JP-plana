from dataclasses import dataclass

@dataclass
class Track:
    title: str
    url: str
    stream_url: str
    duration: int
    thumbnail: str
    requester_id: int