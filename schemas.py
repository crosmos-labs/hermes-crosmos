"""Tool schemas — what the LLM sees."""

CROSMOS_REMEMBER = {
    "name": "crosmos_remember",
    "description": (
        "Store information into the Crosmos memory engine. "
        "Use this when the user shares personal information, preferences, experiences, "
        "instructions, corrections, or any facts worth remembering across conversations. "
        "The content is automatically decomposed into entities and relationships. "
        "Examples: 'User prefers dark mode', 'User works at Anthropic', 'User visited Tokyo last month'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The information to remember. Can be a single fact or a multi-sentence description.",
            },
            "space_name": {
                "type": "string",
                "description": (
                    "Human-readable name of the memory space (optional). "
                    "Pass this when the user mentions a specific space by name. "
                    "If omitted, the default space (CROSMOS_SPACE_NAME) is used."
                ),
            },
        },
        "required": ["content"],
    },
}

CROSMOS_RECALL = {
    "name": "crosmos_recall",
    "description": (
        "Search the Crosmos memory layer for relevant memories. "
        "Use this when the user asks about themselves, past preferences, experiences, "
        "or anything that requires recalling stored context. "
        "Returns scored memories with original source text. "
        "Examples: 'What does the user prefer?', 'Where has the user traveled?', 'What tools does the user use?'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query for relevant memories.",
            },
            "space_name": {
                "type": "string",
                "description": (
                    "Human-readable name of the memory space (optional). "
                    "Pass this when the user mentions a specific space by name. "
                    "If omitted, the default space (CROSMOS_SPACE_NAME) is used."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (1-50, default 10).",
                "default": 10,
            },
            "include_source": {
                "type": "boolean",
                "description": "Include original source text alongside extracted memories (default true).",
                "default": True,
            },
        },
        "required": ["query"],
    },
}

CROSMOS_FORGET = {
    "name": "crosmos_forget",
    "description": (
        "Soft-delete a memory from the knowledge graph. "
        "Use this when the user explicitly asks to remove specific information. "
        "The memory is marked as forgotten and excluded from future searches, but preserved in the graph history."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "string",
                "description": "UUID of the memory to forget (obtained from crosmos_recall results).",
            },
        },
        "required": ["memory_id"],
    },
}

CROSMOS_GRAPH_STATS = {
    "name": "crosmos_graph_stats",
    "description": (
        "Get statistics about the knowledge graph: total entities, edges, "
        "entity type distribution, and top relation types. "
        "Useful for understanding what the agent knows about the user."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "space_name": {
                "type": "string",
                "description": (
                    "Human-readable name of the memory space (optional). "
                    "Pass this when the user mentions a specific space by name. "
                    "If omitted, the default space (CROSMOS_SPACE_NAME) is used."
                ),
            },
        },
        "required": [],
    },
}
