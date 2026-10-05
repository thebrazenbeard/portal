# ChatGPT Exodus Worker Reconstruction Contract

Status: SOURCE DESIGN / NON-ACTIVATING

## Purpose

Project Runner may discover or route work to execution surfaces, but an execution
surface is not a worker's durable identity, memory, authority, or canonical state.

A ChatGPT conversation, Custom GPT page, Work task, API invocation, CLI process,
model call, browser tab, or other runtime is a replaceable terminal.

## Active-registry rule

The committed worker registry must not require a ChatGPT conversation/share URL
for identity or reconstruction.

For `CHATGPT_CUSTOM_GPT` records:
- `gpt_id` may remain as an optional terminal/invocation locator;
- a `share_url` is not required and is excluded from the active seed registry;
- `REGISTERED` plus an unverified route is discovery metadata only;
- a locator never grants authority, proves liveness, or proves executable state.

Historical source documents may preserve old share URLs as provenance. They are
not operational dependencies.

## Worker reconstruction

A durable worker must be reconstructible from durable sources appropriate to its
project, normally including some combination of:
- stable logical worker key/name;
- repository/project scope;
- durable role/authority contract;
- current source/checkpoint/assignment;
- Bus lane or other durable coordination route;
- exact evidence that must be refreshed before work;
- explicit protected-effect limits;
- durable result destination.

Project Runner must not promote a worker to `EXECUTABLE` merely because a ChatGPT
locator exists. Route verification and project authority remain separate gates.

If a worker lacks a durable role/authority contract, dispatch must supply a
bounded assignment whose role and authority are explicit; otherwise the runtime
must fail closed rather than reconstructing the worker from remembered chat.

## Post-Exodus interface topology

Persistent ChatGPT interfaces are human/operator surfaces only. Worker state and
identity remain in GitHub/Bus/project state. No permanent worker chat is required.

This document performs no worker activation, route verification, provider change,
credential change, or protected effect.
