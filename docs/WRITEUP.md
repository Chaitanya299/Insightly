# Approach, decisions, and what I'd build next

**The decision everything follows from.** The obvious build pastes the spreadsheet into the
prompt and asks the model. It states figures it worked out in its head, can't hold a real
file, and can't reliably join two. So here the model writes SQL and DuckDB computes every
number. The model sees a schema card (names, types, null rates, three sample values per
column), never the rows or its own results. The card is 561 characters at 900 rows and at
1,000,000; pasting even the 900-row sample into a prompt is rejected by the free-tier API as too large.

**The delta, measured.** Twenty questions with answers computed independently in pandas,
run against the full system and with one component switched off at a time:

| | Correct |
|---|---|
| Full system | 20 / 20 |
| Without type recovery | 16 / 20 |
| Without date-orientation detection | 16 / 20 |
| Without join hints · definitions · privacy mode on | 20 / 20 each |

Type recovery turns `"$1,234.50"` into a number. Date detection handles `03/05/2024`, which
is 3 May or 5 March and parses either way *without error*. Switched off, the model still
answers every monthly question, confidently and wrongly. That silent failure is the reason
the component exists.

The other three made no difference on this data, and I'd rather say so than claim them.
The key names are obvious, the model's default reading of revenue matches the agreed one,
and the category spellings are guessable. They guard against data the eval set doesn't yet
contain.

**Other deliberate choices.** Generated SQL is untrusted input: a read-only guard, plus
DuckDB with file access switched off. One error-fed repair retry, not a loop. Charts come
from the result's shape, with the model's suggestion checked first. Each file loads in
isolation. The SQL is editable under every answer, so users check the machine instead of
trusting it.

**What went wrong, and what it taught me.** A stubbed test for "decline what the data
can't answer" passed while the real model answered *headcount* with a count of customers.
A stub tests the code around a model, never the model. There is now a live suite. Later,
my own eval harness scored API quota refusals as wrong answers and reported the naive
baseline at 0/20 when it hadn't run. It now records *not run* and stops. Both were the
same bug: treating silence as a result.

**Honest limits.** The three sample values per column do leave the machine; privacy mode
removes them. Throughput is set by the API, not the server: the free tier's 8,000 tokens a
minute is about five questions a minute for the whole app. Single session, no auth.

**What I'd build next.**
1. **A harder eval set, built with the customer:** mismatched key names, a non-obvious
   metric definition, coded categories. That would show whether the three unproven
   components earn their place, and it's the only honest answer to "does it work on our
   data?"
2. **Compile definitions into SQL** instead of asking the model to copy them.
3. **A verification pass** that checks the generated SQL answers the question actually asked.
4. **Persistence, auth and a paid API tier**, because the rate limit is the first wall a
   second user hits.
