import sys
import time

QUESTIONS = [
    "How do bees fly?",
    "whats the name of the last game of thrones episode?",
    # "what to do if there is a pipe leak?",
    # "what 3 restaurants had the best revenue in 2024?",
    # "is smoking prohibited in bujairy?",
    # "What are the 3 top restaurants in revenue in 2024?",
    # "how many reported incidents during December 2024?",
    # "i've a guest lost her child in Bujairi, what should i do?",
    # "how many foreigners visited Bujairi in 2024?",
    # "what's the nationality distribution for guest visiting Bujairi for 2024?",
    # "what should i do if a guest is wearing unsuitable clothes in Bujairi?",
    # "i've water leakage in Bujairi, what should i do?",
    # "what's the SOP for dealing with visitor to bujairi with disability?",
    # "what are the issues listed in the duty managers' log for December 2024?",
    # "What should I do if I suspect a guest is using a fake or counterfeit ticket?",
    # "How do I properly operate a golf cart for transporting guests?",
    # "What is the process if a guest complains about poor service at one of our restaurants?",
    # "What should I do if I notice a tenant violation of our operational guidelines?",
    # "How should staff handle aggressive or intoxicated guests?",
    # "What are the steps for closing down Bujairi Terrace at the end of operating hours?",
    # "What is the procedure for handling complaints about washroom cleanliness?",
    # "How should I handle a situation where a staff member is not following the Green Cleaning Policy?",
    # "What is the procedure for addressing an overflow of visitors beyond capacity?",
    # "What steps should be taken when dealing with an electrical hazard in a public area?",
    # "How do I properly implement service recovery for a guest who has had multiple issues during their visit?",
    # "what's the nationality distribution for guest visiting Bujairi for december 2024?",
    # "how many incidnets we've for december 2024?",
    # "what are the visitations in albujairy each month in 2024?",
    # "What are the areas of improvement for November 2024?",
    # "What are the areas of improvement in Bujairi?",
    # "Is there a difference in welcome experience scores between those that only visited Bujairi and those that visited both?",
    # "How did the restaurants perform? How was dining experience?",
    # "What are the demographic details of guests to Bujairi? What kind of visitors visit Bujairi?",
    # "what to do with a lost child?",
    # "Is there a policy for lost child?",
    # "If someone is found with illegal drugs on site, what is the procedure to address this issue?",
    # "What to do in case of a fire",
    # "How to handle a pipe break",
    # "How to address a visitor not wearing appropriate clothing? (answer should refer to documents in the order - policy, procedure, checklist and then manual) if there is a policy for dress code, then always use policy to answer the question, if not, then refer to procedure, and so on",
    # "Is there any procedural restrictions for items of dress, what is the escalation procedure if someone is restricted from entry and how many people have been restricted in the last year?",
    # "restart chat",
    # "How many cars entered bujairi car park in December?",
    # "What is the monthly total number of cars that have entered bujairi car park in December?",
    # "What is the Bujairy parking volume for the month of December?",
    # "How many days in a month did al Bujairi car park had more cars than Samhan car park?",
    # "How many bujairi visitors we had in 2024?",
    # "What is the number of visitors in Bujairi for 2024?",
    # "can you create a table for monthly bujiari visitors, sales and covers for 2024?",
    # "based on the low performance in June, July and August 2024, do you think it's a good idea to close Bujairi Terrace in those months?",
    # "What is the monthly visitation for October 2024? How does it compare to last year?",
    # "What’s the frequency of belongings’ lost complaints by customers? And what’s the policy for Diriyah to deal with such complaints?",
    # "When did we have the highest visitation? And how many complaints did we get on that date?",
    # "What are the most common incidents reported in the Duty Manager log during holiday periods, and how much higher as a percentage is the volume during a holiday period than the average day?",
    # "Where are we missing duty manager logs?",
    # "What were the most reported issues by duty managers?",
    # "What was the revenue for villa mamas in Nov 2024?",
    # "How many tenants exceeded 10 million in revenue in 2024?",
    # "Which restaurant has been consistently in top 3 positions in terms of revenue every month?",
    # "Who has the least variability in doing operational observations and who has the most?",
    # "Whose observational reports are most closely correlated with the guest feedback for experience strengths and weaknesses?",
    # "What’s the Tenant with recurrent food quality violations? And how this impacting its performance?",
    # "When did we have the lowest attendance? Show avg ticket sales by day of the week? Over months?",
    # "What is the weakest area of experience performance in Bujairi and what are the observation scores for this area?",
    # "How many Palm trees do we have? Where do we have the largest number of palm trees?",
    # "What was the average daily footfall in Al Bujairy Terrace during January 2023?",
    # "On which date in 2023 did Al Bujairy record its highest footfalls?",
    # "Give me the total footfalls for Q2 2023 (Apr 1 – Jun 30) in Al Bujairy.",
    # "How many days in August 2023 did footfalls in Al Bujairy exceed 3000?",
    # "Compare weekday vs. weekend footfalls for October 2023 in Al Bujairy.",
    # "Compare Al Bujairy footfalls on 1 Jan 2023 with the 'Number of tickets' sold that day.",
    # "What were the total 2023 footfalls at Al Bujairy, and what was the average daily ticket revenue for the same year?",
    # "Identify the three busiest days in 2023 for Al Bujairy footfalls and report the gross F&B sales on those days.",
    # "Compare the Quarterly footfalls of Al Bujairy Terrace and Al-Turaif.",
    # "What is the Highest visitation day in Al-bujairy Terrace and what was the footfalls of that day in Al-Turaif?",
    # "How many complaints were logged in January 2024?",
    # "List the top three 'Incident Category' values by complaint count for Q1 2024.",
    # "What is the average resolution time (days) for complaints resolved in 2024?",
    # "Provide a breakdown of complaint 'Status' counts (Resolved vs. Pending) for the last six months.",
    # "Which 'Incident Category' had the longest median resolution time in 2024?",
    # "On days with more than 5 complaints, what were the footfalls at Al-Bujairy Terrace (Al-Bujairy Footfalls.xlsx)?",
    # "Compare the number of complaints per day with the 'Valet Volume' in parking for the same days.",
    # "For complaints marked 'Roads and Infrastructure', list any matching issues logged by Duty Managers on the same dates.",
    # "Give the total complaints in 2024, and the Top2Box score for 'Overall visitor satisfaction' for the same year.",
    # "Provide the count of unresolved complaints as of today and list the most common open-ended visitor comment theme.",
    # "How many incidents did Duty Managers record in June 2024?",
    # "Which department had the highest number of logged issues in 2024?",
    # "What was the average 'Days' to resolution for issues with Status = Pending?",
    # "List all incidents tagged 'Electricity' with their ETAs.",
    # "Provide a shift-wise breakdown (Morning, Evening, Night) of issues for May 2024.",
    # "Match Duty Manager incidents containing the word 'parking' with the corresponding Parking 'Valet Volume' on those dates.",
    # "For every incident classified as 'Operation', show the daily gross F&B sales (Food & Beverages Sales.xlsx) for that date.",
    # "Identify dates where a Duty Manager issue coincided with a Top2Box score below 0.6 for 'Bujairi Terrace experience'.",
    # "How many issues were logged by Abdulrahman Alk… in 2024, and how many of those are still pending?",
    # "List the five longest-open incidents along with the total number of related visitor complaints.",
    # "Which restaurant achieved the highest single-day gross sales in 2023?",
    # "Provide the total gross sales for the Casual Dining category in Q3 2023.",
    # "What was the average covers per day for 'Angelina' in August 2023?",
    # "Calculate the month-over-month sales growth for 'Casual Dining' between Jan and Jun 2023.",
    # "For dates with gross sales > SAR 100 000, show the footfalls at Al-Bujairy Terrace.",
    # "Compare gross sales with the number of tickets sold on the same date (Tickets.xlsx) to identify any correlation.",
    # "Provide a table of Food and beverage gross sales and Top2Box 'Eating out experience' score for each month of 2024.",
    # "What were the total 2023 gross sales for 'Angelina', and what percentage of those days had Valet Utilization above 0.25.",
    # "Give the top three revenue days across all restaurants and list any Duty Manager incidents logged on those days.",
    # "How many observation rows are non-blank?",
    # "List the distribution of Count of Colleagues observed per department.",
    # "Identify any rows labeled Row Labels and explain their purpose.",
    # "Provide a summary of unique observation categories captured in the data.",
    # "Detect and list any data quality issues.",
    # "Correlate observation counts with Top2Box Staff helpfulness scores by month.",
    # "For days with a high observation count, list any related Duty Manager incidents.",
    # "Compare PE observation trends with visitor complaints in the same period.",
    # "How many observations were logged in 2024, and what was the average Valet Utilization on those observation days.",
    # "Provide the three most frequently observed issues and the corresponding Restaurant categories most affected.",
    # "What was the average Valet Volume in January 2023?",
    # "On which dates did al Bujairi Utilization exceed 0.4?",
    # "Provide the total parking revenue (Valet + al Bujairi + Samhan) for each month of 2023.",
    # "Identify the day with zero Samhan Volume but non-zero Samhan Revenue.",
    # "Calculate the correlation between Valet Volume and Valet Revenue for 2023.",
    # "For days where Valet Volume > 300, show the Al-Bujairy Terrace footfalls.",
    # "Compare parking utilization with tickets attendance on weekends in 2023.",
    # "List dates where Valet Utilization < 0.2 and there was a Duty Manager parking incident.",
    # "What was the total Valet Revenue in Eid Al-Adha 2023, and how many Complaints were logged over the same period?",
    # "Provide the daily Valet Volume and gross F&B sales for the top five parking-revenue days.",
    # "What was the total ticket revenue in 2023?",
    # "Calculate the average Free tickets % for weekends in 2023.",
    # "Which day had the highest number of tickets sold in 2023?",
    # "Provide a breakdown of male vs. female attendance for National Day 2023.",
    # "Find the average PM Tickets count for June 2023.",
    # "For days where Free tickets % > 30 %, list the footfalls at Al-Bujairy Terrace.",
    # "Compare the daily ticket revenue with gross F&B sales to spot any correlations.",
    # "Show how Rebate value affects Top2Box Value for money scores by month.",
    # "What were the total tickets sold during Ramadan 2023, and what was the average Valet Utilization in that period?",
    # "List the five days with the highest attendance and the corresponding complaint counts.",
    # "What is the average Top2Box score for Bujairi Terrace overall experience in 2024?",
    # "Identify the month with the lowest score for Eating out experience.",
    # "Provide a trend line of Top2Box scores for Heritage visit experience from Jan 2023 to Dec 2024.",
    # "Which Type shows the greatest variance in scores across the dataset?",
    # "List any months where any Type’s score fell below 0.6.",
    # "Relate drops in Top2Box scores to spikes in visitor complaints.",
    # "Compare Top2Box Bujairi Terrace experience with Al-Bujairy footfalls to see if higher crowds impact satisfaction.",
    # "For months with Top2Box > 0.8 in Eating out experience, display the gross sales growth in F&B.",
    # "What was the average 2024 score for Visitor safety and how many Duty Manager incidents involved safety that year?",
    # "Provide the three lowest monthly scores overall and the corresponding median number of complaints for those months.",
    # "what are the top 3 performers restaurants for Bujairi Terrace in 2024?",
    # "list of revenues of all restaurants classified from best performer to worst performer",
    # "now classify by ocr",
    # "how did you calculate this",
    # "if i want to replace cafe de l'esplanade and tatel, what cuisine types would you suggest",
    # "what cuisine types we have today in Bujairi terrace and what is the gap",
    # "do you think Bujairi terrace director is good?",
    # "what was the total footfall in 2024",
    # "how many visitations do you expect in 2025",
    # "yes",
    # "include avearge check",
    # "how about targeted marketing plan as part of the plan?",
    # "can you estimate the budget required for each plan?",
    # "restart",
    # "what to do if there was a fire, and what is the highest and lowest visits in albujairy in 2024?",
    # "What is the smoking policy",
    # "what is the avg visitation on thursdays at albujairy terrace?",
    # "what to do if there was a fire?",
    # "what is the visitation in albujairy on the 23rd of oct 2023?",
    # "what to do if there was a lost child?",
    # "what is the visitation in albujairy on the 7th of September, and what to do if there was a lost child?",
    # "what to do if there was a lost child, and what is the visitation in albujairy on the 7th of september?",
    # "how to clean the A/C Grills",
    # "can I leave a golf car unattended",
    # "How do bees fly?",
    
    # "what is the policy for a lost child?",
    # "How about for a fire",
    # "what are some missing cuisines in albujairy terrace?",
    # "what are the cuisines that are available in albujairy terrace?",
]

import csv, re, sys, time
from pathlib import Path

USER_ID = "Malsabhan@diriyah.sa"        # ← your RBAC e-mail

# ──────────────────────────────────────────────────────────────
# helper – split “…Source: …” off the tail of the answer
# ──────────────────────────────────────────────────────────────
def split_answer(full_answer: str):
    """
    Returns (pure_answer, source_tag).
    If a trailing 'Source: ...' line exists, it is removed and
    returned separately; otherwise source_tag='Unknown'.
    """
    m = re.search(r"(?i)\n?source:\s*(.*)$", full_answer.strip())
    if m:
        source_tag  = m.group(1).strip()
        pure_answer = full_answer[: m.start()].rstrip()
    else:
        source_tag  = "Unknown"
        pure_answer = full_answer.strip()
    return pure_answer, source_tag

# ──────────────────────────────────────────────────────────────
# CSV initialisation
# ──────────────────────────────────────────────────────────────
csv_path = Path.cwd() / "Testing before handing (no ranking.csv"      # same dir as notebook
write_header = not csv_path.exists()

# ──────────────────────────────────────────────────────────────
# main loop – identical print logic, now with CSV capture
# ──────────────────────────────────────────────────────────────
with csv_path.open("a", newline="", encoding="utf-8") as f_csv:
    writer = csv.writer(f_csv)
    if write_header:
        writer.writerow(["question", "pure_answer", "full_answer", "source"])

    for idx, question in enumerate(QUESTIONS, 1):
        sep = "=" * 80
        print(f"\n{sep}\nQ{idx}: {question}\n{sep}")

        full_chunks = []          # collect streamed chunks

        try:
            for chunk in Ask_Question(question, user_id=USER_ID):
                sys.stdout.write(chunk)      # live stream to notebook
                sys.stdout.flush()
                full_chunks.append(chunk)
        except Exception as e:
            print(f"\n[ERROR] Exception while processing Q{idx}: {e}")
            writer.writerow([question, "", f"[ERROR] {e}", "Error"])
            time.sleep(130)
            continue

        print("\n")                          # newline after answer
        full_answer = "".join(full_chunks).strip()
        pure_answer, src = split_answer(full_answer)

        # ------------- write the CSV row -------------
        writer.writerow([question, pure_answer, full_answer, src])
        f_csv.flush()                         # ensure it’s on disk

        time.sleep(5)                       # throttle like before

print(f"\n✅  All done – results saved to: {csv_path.resolve()}")
   
