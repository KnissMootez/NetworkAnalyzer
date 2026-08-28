# Restoring Claude Code context after a machine wipe

These are Claude's project memory files, copied out of
`C:\Users\<user>\.claude\projects\d--Dev-SmartCareLLM\memory\`.
They are the notes Claude loads at the start of every session.

## To restore on the new machine

1. Clone the repo, then install Claude Code.
2. Recreate the memory directory and copy these files back:

       mkdir -p ~/.claude/projects/d--Dev-SmartCareLLM/memory
       cp .claude-memory/*.md ~/.claude/projects/d--Dev-SmartCareLLM/memory/

   On Windows that path is
   `C:\Users\<user>\.claude\projects\d--Dev-SmartCareLLM\memory\`.
   The folder name is the working directory with separators replaced by
   dashes, so it only matches if the project sits at `D:\Dev\SmartCareLLM`
   again. Put it back in the same place or rename the folder to match.
3. `MEMORY.md` is the index loaded every session. The other files are loaded
   on demand when relevant.

## What these files will NOT bring back

- **Conversation transcripts.** Those live in
  `C:\Users\<user>\.claude\projects\d--Dev-SmartCareLLM\*.jsonl` (~101 MB) and
  are what `claude --continue` and `claude --resume` read. Copy that whole
  folder separately if you want to resume a specific conversation.
- **The databases** (~9 GB, gitignored). Every figure in the report came from
  those exact files.
- **`.env`** with the AWS credentials.

## Also missing after a wipe

- The venv (`networkanalyzer-env/`) — rebuild with
  `pip install -r requirements.txt` if present, otherwise from imports.
- Ollama is gone from this project entirely; inference is AWS Bedrock
  (`qwen.qwen3-32b-v1:0`), so no local models are needed.
