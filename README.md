# Buy or Wait — AI Financial Affordability Agent

An AI-powered financial decision agent that evaluates whether a user can safely afford a requested expense based on their complete financial situation.

> **Example:** “Can I afford this laptop?”

The system goes beyond checking the user's current bank balance. It considers recurring expenses, pending transactions, confirmed income, essential spending, minimum-balance requirements, payment options, and supporting information contained in messages and images.

The goal is to produce a **safe, personalized, and explainable financial recommendation** for every purchase request.

---

## Problem

A purchase cannot be considered affordable simply because the user's current balance is greater than the purchase price.

For every request, the system evaluates:

* Current available balance
* Minimum preferred balance
* Recurring expenses
* Pending payments
* Confirmed income
* Essential spending
* Flexible recurring expenses
* Available payment options
* Installment plans
* Information extracted from messages and images
* Currency conversion requirements
* Desired completion date

The system then determines whether the user should:

* Pay in full
* Make a partial payment
* Use installments
* Wait until a future date
* Avoid the purchase

A recommendation is considered safe only when the complete payment plan can be completed while covering essential expenses and maintaining the user's required minimum balance throughout the forecast period.

---

## Solution

The application reconstructs the user's financial state and performs a forward-looking cash-flow forecast.

### High-Level Flow

```text
                    ┌─────────────────────┐
                    │   Purchase Request  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Load User Data    │
                    │ Profile + Events    │
                    └──────────┬──────────┘
                               │
             ┌─────────────────┼─────────────────┐
             ▼                 ▼                 ▼
      Financial Events      Messages         Images
             │                 │                 │
             └─────────────────┼─────────────────┘
                               ▼
                    ┌─────────────────────┐
                    │ Reconstruct State   │
                    │ Balance / Income /  │
                    │ Expenses / Pending  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Forecast Cash Flow   │
                    │ Across Future Dates │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Evaluate Payment    │
                    │ Options & Constraints│
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Generate Safe Plan  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │     output.csv      │
                    └─────────────────────┘
```

---

## Repository Structure

```text
.
├── README.md
├── problem_statement.md
├── AGENTS.md
│
├── code/
│   └── main.py
│
├── dataset/
│   ├── requests.csv
│   ├── output.csv
│   ├── sample_requests.csv
│   ├── financial_profiles.csv
│   ├── financial_events.csv
│   ├── request_payment_options.csv
│   ├── exchange_rates.csv
│   ├── messages.csv
│   ├── images.csv
│   └── media/
│       └── images/
│
├── evaluation/
│   └── usage_report.md
│
├── output.csv
├── code.zip
└── log.txt
```

---

## Dataset

The system uses multiple data sources to reconstruct each user's financial position.

| File                          | Purpose                                                          |
| ----------------------------- | ---------------------------------------------------------------- |
| `requests.csv`                | Purchase requests that require predictions                       |
| `financial_profiles.csv`      | User balances, minimum balances, priorities and preferences      |
| `financial_events.csv`        | Historical, pending and confirmed financial transactions         |
| `request_payment_options.csv` | Payment options available for each request                       |
| `exchange_rates.csv`          | Fixed dated currency conversion rates                            |
| `messages.csv`                | Supporting information associated with users, requests or events |
| `images.csv`                  | References to financial documents and images                     |
| `media/images/`               | Actual supporting images                                         |
| `sample_requests.csv`         | Solved examples used for validation                              |

Only `requests.csv` requires predictions. The remaining files provide the context required to make the decision.

---

## Decision Logic

For each request, the system calculates the maximum amount that is safe to pay on the request date while protecting:

1. Essential expenses
2. Future committed payments
3. Required minimum balance
4. The user's complete payment schedule

The system then evaluates the available strategies.

### 1. Full Payment

The user can safely pay the complete requested amount immediately.

```text
affordability_status = affordable_now
recommended_payment_method = full_payment
```

### 2. Partial Payment

A partial payment may be recommended when:

* Partial payment is allowed
* The user accepts partial payment
* The amount safe to pay is greater than zero
* The safe amount is less than the requested amount
* The remaining balance can be paid by the desired completion date

The plan contains exactly two payments:

```text
request_date : amount_safe_to_pay
earliest_safe_date : remaining_amount
```

The two payments must equal the original requested amount.

### 3. Installments

If an available installment option provides a safe way to complete the purchase, the system can recommend installments.

Installment schedules must match one of the payment options supplied in the dataset.

### 4. Wait

If the purchase cannot safely be completed immediately but becomes affordable later, the system identifies the earliest safe date for full payment.

```text
affordability_status = affordable_later
recommended_payment_method = wait
```

### 5. Not Recommended

If the purchase cannot be safely completed within the forecast period, the system rejects the purchase recommendation.

```text
affordability_status = not_affordable
recommended_payment_method = not_recommended
```

---

## Financial Forecasting

The core of the system is a forward-looking cash-flow forecast.

The forecast accounts for:

```text
Starting Balance
       +
Confirmed Income
       -
Essential Expenses
       -
Pending Transactions
       -
Recurring Expenses
       -
Purchase Payments
       =
Projected Balance
```

At every relevant point in the forecast:

```text
Projected Balance >= Required Minimum Balance
```

must remain true for a payment plan to be considered safe.

Historical, pending and confirmed events are handled according to their financial status rather than treating all records as immediately available cash. Confirmed salary is counted only on its settlement date, and duplicate representations of the same event must be removed.

---

## Handling Missing Financial Data

A blank transaction amount does **not** mean zero.

When an event has a missing amount, the system uses its `event_id` to locate the corresponding record through `related_event_id` and extracts the required information from the linked image.

Relevant messages, images and payment options are also incorporated into the decision where applicable.

---

## Currency Handling

The dataset contains multiple currencies, including:

* INR
* ZAR
* IDR
* USD
* EUR

Amounts are evaluated using the user's `home_currency`.

Currency conversion uses the fixed, dated exchange rates provided in:

```text
dataset/exchange_rates.csv
```

Live market or banking APIs are not required.

---

## Output

The application generates:

```text
output.csv
```

in the repository root.

Each request produces exactly one prediction.

### Output Columns

| Column                           | Description                                                |
| -------------------------------- | ---------------------------------------------------------- |
| `request_id`                     | Request identifier                                         |
| `amount_safe_to_pay`             | Maximum amount that can safely be paid on the request date |
| `affordability_status`           | Overall affordability decision                             |
| `recommended_payment_method`     | Recommended payment strategy                               |
| `payment_plan`                   | Chronological payment schedule                             |
| `earliest_date_for_full_payment` | Earliest date full payment becomes safe                    |
| `spending_changes_needed`        | Permitted flexible-spending changes                        |
| `decision_explanation`           | Explanation of the financial decision                      |

The required affordability statuses are:

```text
affordable_now
affordable_with_plan
affordable_later
not_affordable
```

The supported payment methods are:

```text
full_payment
partial_payment
installments
wait
not_recommended
```

---

## Validation

Before producing the final output, the system validates the generated predictions.

### Amount Validation

```text
0 <= amount_safe_to_pay <= requested_amount
```

### Payment Plan Validation

* Every installment plan must correspond to a supplied payment option.
* Partial payment plans must contain exactly two payments.
* Payment amounts must sum to the requested amount.
* Payment dates must be chronological.
* The full plan must remain financially safe.

### Spending Change Validation

Only recurring expenses explicitly marked as **flexible** may be modified.

Supported changes include:

```text
stop:<event_id>
reduce_to:<event_id>:<amount>
```

A maximum of three spending changes can be included.

---

## Quick Start

### 1. Clone the repository

```bash
git clone <your-repository-url>
cd <repository-name>
```

### 2. Verify the dataset

Make sure the following directory exists:

```text
dataset/
```

and contains the required input files.

### 3. Run the solution

```bash
python3 code/main.py
```

### 4. Check the output

After execution:

```text
output.csv
```

should exist in the repository root.

Verify that:

* Every request has exactly one prediction
* All required columns are present
* Columns appear in the required order
* Payment plans are valid
* Safe-payment amounts are within bounds

---

## Reproducibility

The solution is designed to be deterministic wherever possible.

All required financial data is provided locally in the repository, including:

* User financial information
* Transactions
* Payment options
* Exchange rates
* Messages
* Supporting images

No live banking connection or live market data is required.

If external AI models or APIs are used, credentials should be supplied through environment variables rather than committed to the repository.

Example:

```bash
export OPENAI_API_KEY="your-key"
```

**Never commit API keys, passwords, tokens or other secrets to Git.**

---

## Evaluation

The generated `output.csv` is evaluated against hidden ground-truth values.

Important scoring areas include:

* `amount_safe_to_pay`
* `affordability_status`
* `recommended_payment_method`
* `payment_plan`
* `earliest_date_for_full_payment`
* `spending_changes_needed`
* `decision_explanation`

---

## Chat Transcript

AI-assisted development is logged in:

```text
log.txt
```

The transcript is maintained at the repository root and should be included with the submission

Do not place secrets, API keys, passwords or other sensitive credentials in the transcript.


---

## Key Design Principle

The system does **not** answer:

> “Does the user have enough money right now?”

Instead, it answers:

> **“Can the user safely complete this purchase while meeting their existing financial commitments and maintaining their required financial buffer?”**

That distinction is the core of the Buy-or-Wait decision engine.

---

