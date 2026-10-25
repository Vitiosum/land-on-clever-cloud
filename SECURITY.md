# Security

This skill makes an agent run commands on an application's repository and on a Clever Cloud account. A security problem can take several forms:
- a command the skill runs (or prints for the agent to run) that executes content of the analysed repository;
- a secret that appears in an output, a deliverable or a file readable by others;
- a command that writes to the source platform (Supabase, Heroku, Vercel…), which must stay read-only;
- a paid resource created without the developer's explicit yes;
- a deploy that overwrites an app that is not the POC's own.

**Do not open a public issue.** Use GitHub private reporting: the repository's *Security* tab, then *Report a vulnerability*. If that button is not available, open an issue without technical details to ask for a contact.

Please give:
- the version (commit);
- the file and line;
- the scenario, from the input to the effect;
- a minimal reproduction, if possible with the fake binaries of `scripts/selftest.sh`.

Known and documented limit (not a vulnerability to report): `clever env set` has no stdin input in clever-tools 5.2.0, so a value is on that command line for the time of the call.
