"""The Sonar assistant: answers questions about stored reports and makes PDFs of them. Read-only, no browser, no shell, no sending.

  safe.py    cleaning of text that came from postings (untrusted) before it reaches the model or the terminal
  tools.py   the six read-only database tools, the two PDF tools (sonar/pdf/) and the read-only database connection
  outbox.py  the files (PDFs) made while answering one question, for the chat to deliver
  agent.py   the system prompt and the conversation (the model loop itself is in agent_runner.py)
  chat.py    the terminal chat; start it with `python -m sonar.assistant`
"""
