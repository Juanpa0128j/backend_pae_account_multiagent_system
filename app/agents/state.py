"""
Agent state definitions for the pilot agent.
Defines the shared state passed between agent nodes.
"""

from typing import Optional, TypedDict, Any


class AgentState(TypedDict):
    """
    State object passed through the agent graph.
    
    Fields:
    - file_path: Path to the PDF file to be processed
    - raw_text: Raw text extracted from the PDF
    - interpreted_data: Structured data extracted by Gemini
    - result: Final JSON result to return to API
    - error: Error message if any step fails
    """
    file_path: str
    raw_text: str
    interpreted_data: dict
    result: dict
    error: Optional[str]
