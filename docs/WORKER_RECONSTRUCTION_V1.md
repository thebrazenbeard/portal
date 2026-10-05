# Project Runner Worker Reconstruction V1

Status: EXODUS HARDENING / SOURCE CONTRACT / NO EXECUTION AUTHORITY

Project Runner treats an execution environment as a terminal, not as durable worker state.

## Core rule

A ChatGPT conversation, Custom GPT page, Work task, CLI session, API invocation, model process, browser tab, or share URL may locate or host an execution attempt. It does not define the worker's durable identity, role, memory, authority, current assignment, or qualification.

LOCATOR != RECONSTRUCTION

ROLE != AUTHORITY

ROUTE_VERIFIED != WORKER_PROFILE_CURRENT

## Lifecycle gate

REGISTERED and DISCOVERED workers may exist as inventory-only records with locators.

A worker may not become PROFILED, CONNECTED, or EXECUTABLE unless its registry record includes an exact immutable GitHub reconstruction binding:

- repository;
- path;
- exact 40-hex commit.

That bound artifact must answer, directly or through referenced current project contracts:

- worker identity or execution-label semantics;
- project/domain;
- allowed and prohibited authority;
- repository and Bus/coordination routes;
- current assignment discovery method;
- controlling source/governance contracts;
- freshness checks required before action;
- durable result destination;
- protected-effect boundaries.

The immutable binding proves which reconstruction artifact was profiled. It does not prove that the artifact is still current. Dispatch must separately refresh project currentness and assignment state.

## UI and provider locators

Fields such as gpt_id, share_url, provider model name, API endpoint, or human handle are transport/discovery locators only.

They may be useful for invocation, but they cannot promote lifecycle, supply worker instructions implicitly, create standing authority, establish current assignment, substitute for exact GitHub reconstruction, or make a lost ChatGPT conversation a recovery dependency.

The twelve seed Custom GPT records remain REGISTERED inventory only. Their share URLs are historical/discovery locators; no role, connectivity, executability, or authority is inferred.

## Assignment model

Standing worker identity and current assignment are separate.

A reconstructible worker may be instantiated in any compatible temporary runtime. The runtime receives only the current bounded assignment and authority supplied by durable Project Runner, source-repository, or Bus evidence. Child work inherits equal or narrower authority only.

A worker with no current durable assignment is idle, not entitled to recover work from conversational memory.

## Exodus interface topology

Persistent ChatGPT interfaces are not worker storage. The intended persistent human interfaces are Vera, Vera Control Plane Coordinator, and BT2 Coordinator.

They may dispatch Project Runner workers from durable state. Additional workers may execute in temporary contexts without acquiring a permanent chat.

## Failure behavior

If a worker's reconstruction artifact is unavailable, mismatched, stale, or ambiguous:

- do not infer it from a chat title, URL, remembered prompt, nickname, or prior model behavior;
- keep the worker below PROFILED, CONNECTED, or EXECUTABLE as applicable;
- surface WORKER_RECONSTRUCTION_GAP;
- preserve any current task as WAITING rather than manufacturing identity or authority.

This contract does not authorize merge, deployment, provider changes, credentials, spend, or other protected effects.
