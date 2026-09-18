# Demo script: Insightly (about 5 minutes)

Live app: https://insightly0.streamlit.app

`[SCREEN]` is what to show. The quoted lines are what to say. Speak at your own pace;
the timings are a guide.

**Before recording:** open the app on a laptop-width window (about 1,300px or wider) and let
it wake up. Free Streamlit apps sleep, so the first load can take 30 seconds. Have these
questions ready to paste:
1. `Average order value by region`
2. `Revenue by month in 2024`
3. `What is our employee headcount?`

---

## 1. The problem (0:00–0:35)

`[SCREEN]` The Upload page, scrolled down to **Why Insightly?**

> "Hi, I'm Chaitanya. This is Insightly: you upload CSV or Excel files and ask questions
> about them in plain English.
>
> The obvious way to build this is to paste the spreadsheet into the prompt and ask the
> model. I measured that. On the same 20 questions it gets 10 right. It does arithmetic in
> its head, it can't hold a real file, and on the full 900-row sample the API rejects the
> request as too large.
>
> So I split the job. The model only writes SQL. DuckDB, a real analytical database, runs it
> and computes every number. Same questions: 20 out of 20, at a quarter of the tokens."

## 2. Upload: messy files in, clean tables out (0:35–1:20)

`[SCREEN]` Scroll up. Click **Load sample files**. Point at the three file cards.

> "Here are three related files: sales, customers and products. They're deliberately messy,
> like real exports.
>
> Each file becomes a table. Look at what was cleaned. Amounts like `$1,234.50` were text,
> and now they're numbers. The order dates mixed three formats, and the app worked out they
> were day-first.
>
> That date detail matters more than it looks. `03/05/2024` is either 3 May or 5 March, and
> both readings parse without any error. A naive parser picks one silently and every monthly
> number is wrong. Insightly looks at the dates that *can't* be ambiguous, like the 20th of
> May, and uses them to decide.
>
> It also found how the files connect: `sales.customer_id` matches `customers.id`. It found
> that by comparing values, not just column names, which is what makes cross-file questions
> work."

## 3. Ask: a verified answer you can check (1:20–2:30)

`[SCREEN]` Click **02 Ask**. Paste `Average order value by region`.

> "Let's ask something that needs two files: average order value by region. Region lives in
> customers, amounts live in sales.
>
> The answer comes back as a chart and a table, with a green stamp: *verified, computed by
> DuckDB*. That stamp is the product's promise. The model never saw these rows; it only saw
> the column names and types.
>
> And this line: *uses the agreed definition of average order value.* Revenue here means
> completed orders only, not refunds. That's the customer's decision, not the model's
> guess."

`[SCREEN]` Open the **SQL** expander.

> "Here's the exact query. You can edit it and re-run it, and the chart updates. As an FDE
> this is what I'd want in front of a customer: when a number is questioned, you don't argue
> with a chatbot, you read the SQL."

`[SCREEN]` Paste `What is our employee headcount?`

> "And when the data can't answer, it says so. There's no employee table, so it declines
> instead of inventing an answer from the customer list. A confident answer to the wrong
> question is the worst failure for a tool like this."

## 4. Dashboard: no model involved (2:30–3:05)

`[SCREEN]` Click **03 Dashboard**. Change **Break down by** to `region`.

> "The dashboard is built from rules, with no model call: headline revenue, latest month
> against the one before, the monthly trend, and a breakdown. Region comes from the customers
> file through the join we saw earlier.
>
> It uses the same agreed definition of revenue, so the dashboard and the chat can never
> disagree on what 'revenue' means. Every query behind it is one click away."

## 5. Data: what you uploaded, and the rules it follows (3:05–3:50)

`[SCREEN]` Click **04 Data**. Point at the `region` row, then scroll to **Agreed definitions**.

> "This page answers 'was my file read correctly?' before you trust any answer. Each column
> gets a small picture: rows per month for dates, the spread for numbers, and the most common
> values with their share for categories. You'd spot a missing month or a broken column here
> first.
>
> Below are the agreed definitions. A word like *revenue* can mean gross, net of refunds, or
> net of discounts. This is where the business fixes it once, as a SQL formula plus a
> plain-English meaning. You can edit them right here, and every formula is test-run against
> the data and through the same safety check before it's saved."

## 6. Quality: proof, not claims (3:50–4:40)

`[SCREEN]` Click **05 Quality**. Show the comparison, then the ablation chart.

> "This is how I know each part earns its place. I wrote questions whose answers I computed
> separately in pandas, then switched off one component at a time.
>
> Without number cleaning, 4 answers go wrong. Without date detection, the same 4 go wrong,
> and silently: the model still answers, confidently, with the wrong month.
>
> I also built a harder set, designed to break things: confusing key names, codes like `EMEA`
> and `CXL`, and a financial year starting in April. The full system gets 17 out of 17.
> Without the agreed definitions it drops to 12.
>
> And the honest part: on the easy data, some components made no difference, and the report
> says so rather than hiding it."

## 7. Safety, limits and what's next (4:40–5:15)

`[SCREEN]` Stay on Quality, or go back to the Upload page.

> "On safety: generated SQL is treated as untrusted input. It must be a single read-only
> query, and the database itself runs with file access switched off, so even a query that
> slipped past my check can't read the disk.
>
> Limits: it's single-user with no login, the free API allows about five questions a minute,
> and a few sample values do reach the model unless privacy mode is on.
>
> Next, I'd build the eval set *with* the customer, on their real data, because that's the
> only honest answer to 'does it work for us?' Thanks for watching."

---

## If you're short on time (2-minute cut)

Keep sections 1, 3 and 6. Say the 10-vs-20 result, show one verified answer with its SQL and
the headcount decline, then show the Quality page.
