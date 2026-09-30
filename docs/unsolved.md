## Not solved yet

### 1. Worker/Workflow config binding

In Cadence, starting a Workflow and running it are separate. A starter command puts the job on a task list, and whatever Worker is listening on that task list picks it up and does the work. The Workflow never records which classifier or model it was meant to use, so the Worker's own config decides. If two Workers with different setups listen on the same task list, or you restart a Worker with a changed config, the same Workflow can quietly switch AI providers partway through.

*Example:* You start a Watch expecting local Laya. Someone restarts the Worker pointed at Jev and forgets to change the task list. The next checks now call the paid Jev API, and nothing in the Workflow tells you it happened.

### 2. Bounded retries across cycles

Each AI call (an Activity) is retried at most 3 times, which is good. But when all 3 attempts fail, the Watch just tries again on the next 15-second cycle, forever. Each Activity's retries are limited, but the Watch as a whole never gives up, raises an alarm, or slows down.

*Example:* Laya's container crashes on Friday night. Over the weekend the Watch tries 3 calls every 15 seconds, about 35,000 failed calls by Monday, and nobody is alerted. If this were a paid API returning errors that count as "temporary," it could also run up cost.

### 3. Idempotent provider calls

Cadence guarantees an Activity runs *at least* once, not *exactly* once. Suppose the AI provider finishes the work but the reply is lost before Cadence records it, for example because of a network blip or a Worker crash. Cadence then retries and calls the provider again. "Idempotent" means a repeated call doesn't do the work twice. Our AI calls have no request ID the provider could use to recognize "I already did this one."

*Example:* Llama writes the impact report, the Worker crashes before saving the result, and Cadence reruns it. You pay for two reports but keep only one. For a model call that's just extra cost. If the Activity instead sent a Slack alert or opened a ticket, the team would get it twice.
