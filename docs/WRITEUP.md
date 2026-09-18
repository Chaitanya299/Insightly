# Approach, decisions, and what I'd build next

**The decision everything follows from.** The obvious build pastes the spreadsheet into the
prompt and asks the model. It states figures it worked out in its head, can't hold a real
file, and can't reliably join two. So here the model writes SQL and DuckDB computes every
number. The model sees a schema card (names, types, null rates, three sample values or the
full list for a small category column), never the rows or its own results. The card is 564 characters at 900 rows and at
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

The other three made no difference on that data: the key names are obvious and the
category spellings guessable. So I built a second, 17-question set where each one is the
only thing standing between the model and a wrong answer (mismatched keys, revenue net of
discount with an April fiscal year, codes like `CXL` and `EMEA`):

| Hard set | Correct |
|---|---|
| Full system | 17 / 17 |
| Without definitions | 12 / 17 |
| Privacy mode on | 13 / 17 |
| Privacy mode, without join hints | 10 / 17 |

Definitions earn their place outright. Join hints matter once privacy mode hides the
values the model would otherwise match by eye. Privacy mode costs the coded filters.

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

**Honest limits.** Sample values (and the full list for small category columns) do leave
the machine; privacy mode removes them, at the cost above. Throughput is set by the API, not the server: the free tier's 8,000 tokens a
minute is about five questions a minute for the whole app. Single session, no auth.

**What I'd build next.**
1. **An eval set built with the customer, on their data**: the only honest answer to
   "does it work on ours?"
2. **Compile definitions into SQL** instead of asking the model to copy them.
3. **A verification pass** that checks the generated SQL answers the question actually asked.
4. **Persistence, auth and a paid API tier**, because the rate limit is the first wall a
   second user hits.
