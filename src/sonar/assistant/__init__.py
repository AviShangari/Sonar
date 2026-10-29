"""The Sonar assistant: answers questions about stored reports. Read-only, no browser, no shell, no sending.

  safe.py    cleaning of text that came from postings (untrusted) before it reaches the model or the terminal
  tools.py   the six read-only database tools and the read-only database connection
  agent.py   the system prompt and the conversation (the model loop itself is in agent_runner.py)
  chat.py    the terminal chat; start it with `python -m sonar.assistant`
"""
