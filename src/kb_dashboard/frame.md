# Kart dashboard frame

This file holds the dashboard’s interaction and design rules. Add new rules here as the interface develops.

## Every button must respond

A tap must either perform the action, show that confirmation is pending, or explain why the action cannot run. When blocked or rejected, light the pressed button red and show a readable message naming the reason and the next useful step. Keep the message visible until another action replaces it. A grey button or hover-only tooltip is insufficient, especially on a phone.

Keep unavailable actions reachable for this explanation while guarding against sending a blocked command. Show success only after actual confirmation; sending a request is not confirmation. Apply the same feedback to connection loss, stale status, safety faults, unavailable features, and actions that would leave the state unchanged.

Check blocked taps, rejected requests, missing confirmation, and a successful action on both laptop and landscape-phone layouts. The explanation must appear without permitting movement or clearing a safety latch.
