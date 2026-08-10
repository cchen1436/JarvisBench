# Cloudflare Outage — 2022-06-21 (BGP misconfiguration)

Incident date: 2022-06-21
Source: Cloudflare public postmortem (https://blog.cloudflare.com/cloudflare-outage-on-june-21-2022/).


## Cloudflare outage on June 21, 2022
2022-06-21
- Tom Strickx
- Jeremy Hartman

## Introduction
Today, June 21, 2022, Cloudflare suffered an outage that affected traffic in 19 of our data centers. Unfortunately, these 19 locations handle a significant proportion of our global traffic. This outage was caused by a change that was part of a long-running project to increase resilience in our busiest locations. A change to the network configuration in those locations caused an outage which starte…
Depending on your location in the world you may have been unable to access websites and services that rely on Cloudflare. In other locations, Cloudflare continued to operate normally.
We are very sorry for this outage. This was our error and not the result of an attack or malicious activity.

## Background
Over the last 18 months, Cloudflare has been working to convert all of our busiest locations to a more flexible and resilient architecture. In this time, we’ve converted 19 of our data centers to this architecture, internally called Multi-Colo PoP (MCP): Amsterdam, Atlanta, Ashburn, Chicago, Frankfurt, London, Los Angeles, Madrid, Manchester, Miami, Milan, Mumbai, Newark, Osaka, São Paulo, San Jos…
A critical part of this new architecture, which is designed as a Clos network , is an added layer of routing that creates a mesh of connections. This mesh allows us to easily disable and enable parts of the internal network in a data center for maintenance or to deal with a problem. This layer is represented by the spines in the following diagram.
This new architecture has provided us with significant reliability improvements, as well as allowing us to run maintenance in these locations without disrupting customer traffic. As these locations also carry a significant proportion of the Cloudflare traffic, any problem here can have a very wide impact, and unfortunately, that’s what happened today.

## Incident timeline and impact
In order to be reachable on the Internet, networks like Cloudflare make use of a protocol called BGP . As part of this protocol, operators define policies which decide which prefixes (a collection of adjacent IP addresses) are advertised to peers (the other networks they connect to), or accepted from peers.
These policies have individual components, which are evaluated sequentially. The end result is that any given prefixes will either be advertised or not advertised. A change in policy can mean a previously advertised prefix is no longer advertised, known as being "withdrawn", and those IP addresses will no longer be reachable on the Internet.
While deploying a change to our prefix advertisement policies, a re-ordering of terms caused us to withdraw a critical subset of prefixes.
Due to this withdrawal, Cloudflare engineers experienced added difficulty in reaching the affected locations to revert the problematic change. We have backup procedures for handling such an event and used them to take control of the affected locations.
03:56 UTC : We deploy the change to our first location. None of our locations are impacted by the change, as these are using our older architecture. 06:17 : The change is deployed to our busiest locations, but not the locations with the MCP architecture. 06:27 : The rollout reached the MCP-enabled locations, and the change is deployed to our spines. This is when the incident started , as this swif…
The criticality of these data centers can clearly be seen in the volume of successful HTTP requests we handled globally:
Even though these locations are only 4% of our total network, the outage impacted 50% of total requests. The same can be seen in our egress bandwidth:

## Technical description of the error and how it happened
As part of our continued effort to standardize our infrastructure configuration, we were rolling out a change to standardize the BGP communities we attach to a subset of the prefixes we advertise. Specifically, we were adding informational communities to our site-local prefixes.
These prefixes allow our metals to communicate with each other, as well as connect to customer origins. As part of the change procedure at Cloudflare, a Change Request ticket was created, which includes a dry-run of the change, as well as a stepped rollout procedure. Before it was allowed to go out, it was also peer reviewed by multiple engineers. Unfortunately, in this case, the steps weren’t sma…
The change looked like this on one of the routers:
This was harmless, and just added some additional information to these prefix advertisements. The change on the spines was the following:
An initial glance at this diff might give the impression that this change is identical, but unfortunately, that’s not the case. If we focus on one part of the diff, it might become clear why:
In this diff format, the exclamation marks in front of the terms indicate a re-ordering of the terms. In this case, multiple terms moved up, and two terms were added to the bottom. Specifically, the 4-ADV-SITE-LOCALS and 6-ADV-SITE-LOCALS terms moved from the top to the bottom. These terms were now behind the REJECT-THE-REST term, and as might be clear from the name, this term is an explicit rejec…
As this term is now before the site-local terms, we immediately stopped advertising our site-local prefixes, removing our direct access to all the impacted locations, as well as removing the ability of our servers to reach origin servers.
On top of the inability to contact origins, the removal of these site-local prefixes also caused our internal load balancing system Multimog (a variation of our Unimog load-balancer ) to stop working, as it could no longer forward requests between the servers in our MCPs. This meant that our smaller compute clusters in an MCP received the same amount of traffic as our largest clusters, causing the…

## Remediation and follow-up steps
This incident had widespread impact, and we take availability very seriously. We have identified several areas of improvement and will continue to work on uncovering any other gaps that could cause a recurrence.
Here is what we are working on immediately:
Process : While the MCP program was designed to improve availability, a procedural gap in how we updated these data centers ultimately caused a broader impact in MCP locations specifically. While we did use a stagger procedure for this change, the stagger policy did not include an MCP data center until the final step. Change procedures and automation need to include MCP-specific test and deploy pr…
Architecture : The incorrect router configuration prevented the proper routes from being announced, preventing traffic from flowing properly to our infrastructure. Ultimately the policy statement that caused the incorrect routing advertisement will be redesigned to prevent an unintentional incorrect ordering.
Automation : There are several opportunities in our automation suite that would mitigate some or all of the impact seen from this event. Primarily, we will be concentrating on automation improvements that enforce an improved stagger policy for rollouts of network configuration and provide an automated “commit-confirm” rollback. The former enhancement would have significantly lessened the overall i…

## Conclusion
Although Cloudflare has invested significantly in our MCP design to improve service availability, we clearly fell short of our customer expectations with this very painful incident. We are deeply sorry for the disruption to our customers and to all the users who were unable to access Internet properties during the outage. We have already started working on the changes outlined above and will conti…

## Root causes
(see analysis section above)
