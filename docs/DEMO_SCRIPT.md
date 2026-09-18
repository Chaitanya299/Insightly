# Demo script: Insightly (about 5 minutes)

Live app: https://insightly0.streamlit.app

`[SCREEN]` is what to show. The quoted text is what to say. It's written the way you'd
explain it to a colleague sitting next to you, so don't read it word for word; use it as
a guide and say it in your own words.

**Before recording:** open the app in a laptop-width window and let it wake up. Free
Streamlit apps go to sleep, so the first load can take about 30 seconds. Keep these three
questions ready to paste:
1. `Average order value by region`
2. `Revenue by month in 2024`
3. `What is our employee headcount?`

---

## 1. The problem (0:00–0:40)

`[SCREEN]` The Upload page, scrolled down to **Why Insightly?**

> "Hi, I'm Chaitanya, and this is Insightly. The idea is simple: you upload a few CSV or
> Excel files and ask questions about them in plain English, the way you'd ask an analyst.
>
> When I started, the first thing I tried was the approach most people reach for, which is
> to paste the spreadsheet into the prompt and let the model answer. I wanted to know how
> well that actually works, so I wrote 20 questions, worked out the correct answers
> separately, and tested it. It got about half of them right. When I looked at the misses,
> the model was doing the arithmetic itself and getting it wrong, and with the full 900-row
> file the request didn't even go through because it was too large for the API.
>
> So I changed who does what. The model's only job is to turn your question into a SQL
> query. The query then runs in DuckDB, which is an analytical database, and that's what
> produces every number you see. With that change the same 20 questions all came back
> correct, and each question used about a quarter of the tokens."

## 2. Upload: making messy files usable (0:40–1:30)

`[SCREEN]` Scroll up, click **Load sample files**, then point at the three file cards.

> "I'll load some sample data. These are three related files: sales, customers and products.
> I made them messy on purpose, because real exports usually are.
>
> Each file becomes a table, and the card tells you what had to be fixed. The amounts came
> in as text with dollar signs and commas, so they've been converted to numbers. The order
> dates were in three different formats, and the app has worked out that they're day-first.
>
> That date part is worth a moment, because it's the kind of bug nobody notices. A date like
> 03/05/2024 could be the 3rd of May or the 5th of March, and either reading is perfectly
> valid, so there's no error to warn you. If a tool guesses wrong, every monthly total is
> off and you'd never know. What Insightly does is look at the dates that can only be read
> one way, like the 20th of May, and use those to decide how to read the rest.
>
> Down here it also shows how the files connect: the customer ID in sales matches the ID in
> customers. It finds that by comparing the actual values in the columns rather than trusting
> the column names, and that's what lets one question pull from more than one file."

## 3. Ask: an answer you can check (1:30–2:40)

`[SCREEN]` Click **02 Ask** and paste `Average order value by region`.

> "Let me ask something that needs two of those files: the average order value by region.
> The region is stored with the customers, but the order amounts are in sales, so it has to
> join them.
>
> I get a chart and a table, and there's a small green label saying the result was computed
> by DuckDB. That matters to me because the model never saw these rows at all. It only saw
> the column names, their types and a few example values, and it wrote a query.
>
> There's also a line saying it used the agreed definition of average order value, which
> here means completed orders only, with refunds left out. That's a business decision, and
> I didn't want the model making it differently each time someone asks."

`[SCREEN]` Open the **SQL** section.

> "And this is the exact query that produced the answer. You can change it and run it again
> right here. When I picture putting this in front of a customer, this is the part I care
> about most: if someone doubts a number, they can read how it was calculated instead of
> just taking the AI's word for it."

`[SCREEN]` Paste `What is our employee headcount?`

> "Now something the data can't answer. There's nothing about employees in these files, and
> it tells me that plainly. An early version of this actually answered by counting
> customers, which is exactly the kind of confident wrong answer I wanted to rule out."

## 4. Dashboard: no AI involved (2:40–3:15)

`[SCREEN]` Click **03 Dashboard**, then change **Break down by** to `region`.

> "The dashboard is built without the model at all. It picks the main numbers from the data
> using simple rules: total revenue, how the latest month compares with the one before, the
> trend over time, and a breakdown I can change. Here I'm breaking it down by region, which
> again comes from the customers file through that link we saw earlier.
>
> It uses the same definition of revenue as the chat, so the two can't give you different
> numbers for the same thing. The queries behind it are listed at the bottom if you want to
> check them."

## 5. Data: checking the upload and setting the rules (3:15–4:00)

`[SCREEN]` Click **04 Data**, point at the `region` row, then scroll to **Agreed definitions**.

> "This page is about confidence in the input. For every column there's a small picture and
> a short description: for dates it's how many rows fall in each month, for numbers it's how
> the values are spread out, and for categories it's which values are most common and their
> share. If a month were missing or a column had been read wrongly, this is where you'd
> notice before trusting any answer.
>
> Further down are the agreed definitions. A word like 'revenue' can mean gross sales,
> sales after refunds, or sales after discounts, and different teams often mean different
> things. This is where the business writes down which one it means, as a formula plus a
> plain-English explanation. You can edit them here, and before anything is saved the app
> runs the formula against the data to make sure it actually works."

## 6. Quality: how I know it works (4:00–4:45)

`[SCREEN]` Click **05 Quality**. Show the comparison, then the chart below it.

> "I didn't want to just claim each piece is useful, so I tested it. I took my question set,
> switched off one part at a time, and counted what broke.
>
> Without the number cleaning, four answers went wrong. Without the date detection, the same
> four went wrong, and that one worries me more, because the model still answered them
> confidently; it was just using the wrong months.
>
> The sample data turned out to be too easy for some parts, so I built a second, harder set
> meant to trip things up: columns with misleading names, codes like EMEA instead of
> 'Europe', and a financial year that starts in April. The full system got all 17 right.
> Without the agreed definitions it dropped to 12. I've also kept the results where
> switching something off made no difference, because I'd rather show that than overstate
> what each part does."

## 7. Safety, limits and what I'd do next (4:45–5:20)

`[SCREEN]` Stay on Quality or go back to Upload.

> "On safety, I treat the query the model writes as something that can't be trusted. It's
> only allowed to read data, one query at a time, and the database itself is set up so it
> can't open files on the machine. So even if a bad query slipped past my check, it still
> couldn't do any damage.
>
> There are some honest limits. It's built for one person at a time with no login, the free
> API tier handles about five questions a minute, and a few example values from each column
> are sent to the model unless you switch on privacy mode.
>
> If I were taking this to a real customer, the first thing I'd do is build the test set
> with them, using their own data and the questions they actually ask, because that's the
> only real way to answer 'will this work for us?' Thanks for watching."

---

## Short version (about 2 minutes)

Use sections 1, 3 and 6. Explain the half-right result from pasting data into the prompt,
show one answer with its SQL and the headcount question, then finish on the Quality page.
