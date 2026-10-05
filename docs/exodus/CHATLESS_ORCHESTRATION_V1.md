# Chatless Orchestration V1

Status: EXODUS_CANDIDATE
Date: 2026-09-19

## Purpose

Project Runner must not depend on persistent ChatGPT conversations as infrastructure.

A worker may execute inside a temporary ChatGPT context, Work task, API runtime, CLI process, model invocation, subagent, or other terminal. The terminal is not the worker's durable identity, memory, authority, assignment, or canonical state.

## Persistent human interfaces

The intended persistent ChatGPT interface topology is exactly:

- Vera
- Vera Control Plane Coordinator
- BT2 Coordinator

These are human interfaces to durable state. They are not canonical stores.

## Durable worker rule

A worker is reconstructible only when durable GitHub/Bus state is sufficient to determine, directly or by repository convention:

- stable worker/role identity;
- project/domain;
- governing source/contracts;
- repository and Bus route;
- current assignment/frontier;
- authority ceiling and explicit prohibitions;
- current versus historical state;
- exact evidence to refresh before acting;
- where results must be persisted;
- what protected effects still require Patrick.

A chat URL, conversation ID, title, retained browser tab, or inaccessible transcript may be historical provenance, but may not be required for reconstruction.

## Project Runner boundary

Project Runner may discover, classify, prioritize, dispatch, and verify work only from durable state and current evidence.

Portfolio inclusion does not make Project Runner a shared monolith and does not transfer repository authority. Discovery is observational by default.

A repository can remain architecturally independent while still being discoverable by Project Runner.

`DISCOVERABLE != COUPLED`

`COORDINATED != AUTHORIZED`

`EXECUTABLE_ROUTE != TARGET_AUTHORITY`

## Terminal lifecycle

A future runtime should be able to:

1. identify the worker from durable sources;
2. fresh-read target state;
3. obtain a bounded assignment;
4. execute inside an ephemeral terminal;
5. persist result/evidence;
6. terminate the terminal without losing durable worker identity or current work.

## Current architectural gap

The current Project Runner worker registry is strongest for registered Custom GPT locators and does not yet represent every durable GitHub/Bus-defined worker, reviewer, or project role.

M6 should close that gap without treating chat sessions as worker records.

## Reconstruction test

A worker passes the Exodus reconstruction test when Vera, Vera Control Plane Coordinator, or BT2 Coordinator can instantiate it tomorrow using only current Project instructions where applicable, GitHub, current Bus state, durable worker/project contracts, checkpoints, and authorized provider reads.

If the operator must recover an archived chat to know what the worker is or what it should do next, reconstruction fails.
