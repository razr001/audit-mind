-- AuditMind PostgreSQL 初始表结构
-- Alembic revision: 0001_initial_schema
-- Generated from a clean PostgreSQL 18 database after running the former
-- migration history. This file is executed by the first Alembic migration.
--
-- This file creates schema objects only; it does not create application users
-- or insert business data. Run it through "alembic upgrade head" instead of
-- importing it with psql, because Alembic owns the alembic_version table.

-- Dumped from database version 18.6 (Debian 18.6-1.pgdg13+2)
-- Dumped by pg_dump version 18.6 (Debian 18.6-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: assistant_action_risk; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.assistant_action_risk AS ENUM (
    'WRITE',
    'DELETE',
    'ADMIN'
);


--
-- Name: assistant_action_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.assistant_action_status AS ENUM (
    'PENDING',
    'APPROVED',
    'REJECTED',
    'EXPIRED',
    'EXECUTING',
    'SUCCEEDED',
    'FAILED',
    'PARTIAL',
    'RECONCILIATION_REQUIRED'
);


--
-- Name: assistant_agent_run_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.assistant_agent_run_status AS ENUM (
    'RUNNING',
    'WAITING_APPROVAL',
    'COMPLETED',
    'FAILED',
    'CANCELED',
    'EXPIRED'
);


--
-- Name: assistant_message_role; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.assistant_message_role AS ENUM (
    'USER',
    'ASSISTANT'
);


--
-- Name: assistant_message_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.assistant_message_status AS ENUM (
    'GENERATING',
    'COMPLETED',
    'FAILED',
    'CANCELED',
    'WAITING_APPROVAL'
);


--
-- Name: assistant_tool_call_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.assistant_tool_call_status AS ENUM (
    'RUNNING',
    'SUCCEEDED',
    'FAILED',
    'REJECTED',
    'RECONCILIATION_REQUIRED'
);


--
-- Name: auditstage; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.auditstage AS ENUM (
    'UPLOADING',
    'PARSING',
    'INDEXING',
    'AUDITING',
    'COMPLETED'
);


--
-- Name: auditstatus; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.auditstatus AS ENUM (
    'CREATED',
    'RUNNING',
    'COMPLETED',
    'FAILED',
    'PARTIAL_FAILED'
);


--
-- Name: audittaskpagestatus; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.audittaskpagestatus AS ENUM (
    'PENDING',
    'RUNNING',
    'COMPLETED',
    'FAILED'
);


--
-- Name: documentstatus; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.documentstatus AS ENUM (
    'UPLOADED',
    'PARSING',
    'READY',
    'FAILED'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: app_user; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_user (
    username character varying(64) NOT NULL,
    password_hash character varying(255) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: assistant_action; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assistant_action (
    run_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    user_id uuid NOT NULL,
    tool_call_id character varying(128) NOT NULL,
    tool_name character varying(100) NOT NULL,
    risk_level public.assistant_action_risk NOT NULL,
    arguments json NOT NULL,
    arguments_hash character varying(64) NOT NULL,
    display_summary character varying(500) NOT NULL,
    status public.assistant_action_status NOT NULL,
    version integer NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    decided_at timestamp with time zone,
    executed_at timestamp with time zone,
    resource_type character varying(64),
    resource_id uuid,
    result_code character varying(64),
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    reconciled_at timestamp with time zone,
    reconciled_by uuid,
    reconciliation_note text
);


--
-- Name: assistant_agent_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assistant_agent_run (
    conversation_id uuid NOT NULL,
    assistant_message_id uuid NOT NULL,
    user_id uuid NOT NULL,
    thread_id character varying(100) NOT NULL,
    status public.assistant_agent_run_status NOT NULL,
    intent character varying(64),
    model_call_count integer NOT NULL,
    tool_call_count integer NOT NULL,
    lock_version integer NOT NULL,
    error_code character varying(64),
    completed_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: assistant_conversation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assistant_conversation (
    user_id uuid NOT NULL,
    title character varying(100) NOT NULL,
    last_message_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: assistant_message; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assistant_message (
    conversation_id uuid NOT NULL,
    role public.assistant_message_role NOT NULL,
    content text NOT NULL,
    status public.assistant_message_status NOT NULL,
    answered boolean,
    sources json NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: assistant_tool_call; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assistant_tool_call (
    run_id uuid NOT NULL,
    tool_call_id character varying(128) NOT NULL,
    tool_name character varying(100) NOT NULL,
    arguments_hash character varying(64) NOT NULL,
    idempotency_key character varying(64) NOT NULL,
    status public.assistant_tool_call_status NOT NULL,
    result_code character varying(64),
    resource_type character varying(64),
    resource_id uuid,
    retry_count integer NOT NULL,
    completed_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_task; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_task (
    document_id uuid NOT NULL,
    status public.auditstatus NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    error text,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    stage public.auditstage DEFAULT 'UPLOADING'::public.auditstage NOT NULL,
    total_pages integer DEFAULT 0 NOT NULL,
    completed_pages integer DEFAULT 0 NOT NULL,
    finding_count integer DEFAULT 0 NOT NULL,
    rule_scope json DEFAULT '{}'::json NOT NULL,
    audit_as_of date DEFAULT CURRENT_DATE NOT NULL,
    lock_version bigint DEFAULT 0 NOT NULL,
    agent_tool_call_id uuid
);


--
-- Name: audit_task_page; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_task_page (
    task_id uuid NOT NULL,
    page_number integer NOT NULL,
    status public.audittaskpagestatus NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    finding_count integer DEFAULT 0 NOT NULL,
    error text,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: document; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document (
    status public.documentstatus NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    user_id uuid NOT NULL,
    original_filename character varying(255) NOT NULL,
    storage_key character varying(512) NOT NULL,
    content_type character varying(128) NOT NULL,
    file_size bigint NOT NULL,
    parse_task_id character varying(100),
    parse_error text,
    parse_started_at timestamp with time zone,
    parse_completed_at timestamp with time zone,
    lock_version bigint DEFAULT 0 NOT NULL,
    source_type character varying(32) DEFAULT 'PDF'::character varying NOT NULL
);


--
-- Name: document_page; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_page (
    document_id uuid NOT NULL,
    page_number integer NOT NULL,
    content text NOT NULL,
    bbox json,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: document_parse_block; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_parse_block (
    document_id uuid NOT NULL,
    block_index integer NOT NULL,
    block_type character varying(50) NOT NULL,
    content text NOT NULL,
    page_number integer,
    bbox json,
    text_level integer,
    char_start integer NOT NULL,
    char_end integer NOT NULL,
    block_metadata json,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evidence (
    finding_id uuid NOT NULL,
    page_number integer NOT NULL,
    quote text NOT NULL,
    bbox json,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    document_block_id uuid,
    char_start integer,
    char_end integer
);


--
-- Name: finding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.finding (
    task_id uuid NOT NULL,
    level character varying(20) NOT NULL,
    title character varying(255) NOT NULL,
    description text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    task_page_id uuid,
    page_number integer,
    recommendation text
);


--
-- Name: finding_rule_reference; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.finding_rule_reference (
    finding_id uuid NOT NULL,
    regulation_rule_id uuid NOT NULL,
    regulation_id uuid NOT NULL,
    rule_type character varying(50) NOT NULL,
    topic character varying(255),
    rule_summary text NOT NULL,
    rule_snapshot json NOT NULL,
    source_filename character varying(255) NOT NULL,
    source_content_hash character varying(64) NOT NULL,
    source_page_start integer,
    source_page_end integer,
    source_text text NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: operation_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operation_log (
    user_id uuid NOT NULL,
    operation_type character varying(50) NOT NULL,
    target_type character varying(50) NOT NULL,
    target_id uuid NOT NULL,
    parent_id uuid,
    request_id character varying(128),
    before_data json,
    after_data json,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: regulation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.regulation (
    title character varying(255) NOT NULL,
    document_number character varying(100),
    authority character varying(255),
    jurisdiction character varying(100) NOT NULL,
    effective_date date,
    expiration_date date,
    version character varying(50),
    source_url character varying(1000),
    storage_key character varying(512) NOT NULL,
    original_filename character varying(255) NOT NULL,
    content_type character varying(128) NOT NULL,
    file_size bigint NOT NULL,
    content_hash character varying(64) NOT NULL,
    uploaded_by uuid NOT NULL,
    enabled boolean NOT NULL,
    status character varying(20) NOT NULL,
    parse_task_id character varying(100),
    parse_error text,
    parse_started_at timestamp with time zone,
    parse_completed_at timestamp with time zone,
    chunk_status character varying(20) CONSTRAINT regulation_knowledge_status_not_null NOT NULL,
    chunk_error text,
    chunk_started_at timestamp with time zone,
    chunk_completed_at timestamp with time zone,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    source_type character varying(50) NOT NULL,
    category character varying(50) NOT NULL,
    visibility character varying(20) NOT NULL,
    language character varying(20) NOT NULL,
    lock_version bigint DEFAULT 0 NOT NULL,
    index_status character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    index_error text,
    index_started_at timestamp with time zone,
    index_completed_at timestamp with time zone,
    rule_status character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    rule_error text,
    rule_started_at timestamp with time zone,
    rule_completed_at timestamp with time zone,
    agent_tool_call_id uuid
);


--
-- Name: regulation_chunk; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.regulation_chunk (
    regulation_id uuid NOT NULL,
    chunk_index integer NOT NULL,
    article_number character varying(100),
    chapter character varying(255),
    page_number integer,
    char_start integer,
    char_end integer,
    content text NOT NULL,
    chunk_metadata json,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: regulation_parse_block; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.regulation_parse_block (
    regulation_id uuid NOT NULL,
    block_index integer NOT NULL,
    block_type character varying(50) NOT NULL,
    content text NOT NULL,
    page_number integer,
    bbox json,
    text_level integer,
    char_start integer NOT NULL,
    char_end integer NOT NULL,
    block_metadata json,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: regulation_rule; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.regulation_rule (
    regulation_id uuid NOT NULL,
    source_chunk_id uuid NOT NULL,
    source_block_ids json NOT NULL,
    rule_index integer NOT NULL,
    rule_type character varying(30) NOT NULL,
    topic text,
    subject text,
    action text,
    object text,
    condition text,
    time_limit text,
    requirements json NOT NULL,
    restrictions json NOT NULL,
    exceptions json NOT NULL,
    consequences json NOT NULL,
    payload json NOT NULL,
    source_filename character varying(255) NOT NULL,
    source_content_hash character varying(64) NOT NULL,
    source_page_start integer,
    source_page_end integer,
    source_char_start integer NOT NULL,
    source_char_end integer NOT NULL,
    source_text text NOT NULL,
    extractor_profile character varying(100) NOT NULL,
    extractor_version character varying(50) NOT NULL,
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: app_user app_user_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_user
    ADD CONSTRAINT app_user_pkey PRIMARY KEY (id);


--
-- Name: assistant_action assistant_action_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_action
    ADD CONSTRAINT assistant_action_pkey PRIMARY KEY (id);


--
-- Name: assistant_agent_run assistant_agent_run_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_agent_run
    ADD CONSTRAINT assistant_agent_run_pkey PRIMARY KEY (id);


--
-- Name: assistant_agent_run assistant_agent_run_thread_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_agent_run
    ADD CONSTRAINT assistant_agent_run_thread_id_key UNIQUE (thread_id);


--
-- Name: assistant_conversation assistant_conversation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_conversation
    ADD CONSTRAINT assistant_conversation_pkey PRIMARY KEY (id);


--
-- Name: assistant_message assistant_message_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_message
    ADD CONSTRAINT assistant_message_pkey PRIMARY KEY (id);


--
-- Name: assistant_tool_call assistant_tool_call_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_tool_call
    ADD CONSTRAINT assistant_tool_call_pkey PRIMARY KEY (id);


--
-- Name: audit_task_page audit_task_page_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_task_page
    ADD CONSTRAINT audit_task_page_pkey PRIMARY KEY (id);


--
-- Name: audit_task audit_task_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_task
    ADD CONSTRAINT audit_task_pkey PRIMARY KEY (id);


--
-- Name: document_page document_page_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_page
    ADD CONSTRAINT document_page_pkey PRIMARY KEY (id);


--
-- Name: document_parse_block document_parse_block_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_parse_block
    ADD CONSTRAINT document_parse_block_pkey PRIMARY KEY (id);


--
-- Name: document document_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document
    ADD CONSTRAINT document_pkey PRIMARY KEY (id);


--
-- Name: document document_storage_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document
    ADD CONSTRAINT document_storage_key_key UNIQUE (storage_key);


--
-- Name: evidence evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_pkey PRIMARY KEY (id);


--
-- Name: finding finding_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finding
    ADD CONSTRAINT finding_pkey PRIMARY KEY (id);


--
-- Name: finding_rule_reference finding_rule_reference_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finding_rule_reference
    ADD CONSTRAINT finding_rule_reference_pkey PRIMARY KEY (id);


--
-- Name: operation_log operation_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operation_log
    ADD CONSTRAINT operation_log_pkey PRIMARY KEY (id);


--
-- Name: regulation_chunk regulation_chunk_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regulation_chunk
    ADD CONSTRAINT regulation_chunk_pkey PRIMARY KEY (id);


--
-- Name: regulation_parse_block regulation_parse_block_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regulation_parse_block
    ADD CONSTRAINT regulation_parse_block_pkey PRIMARY KEY (id);


--
-- Name: regulation regulation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regulation
    ADD CONSTRAINT regulation_pkey PRIMARY KEY (id);


--
-- Name: regulation_rule regulation_rule_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regulation_rule
    ADD CONSTRAINT regulation_rule_pkey PRIMARY KEY (id);


--
-- Name: regulation regulation_storage_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regulation
    ADD CONSTRAINT regulation_storage_key_key UNIQUE (storage_key);


--
-- Name: assistant_action uq_assistant_action_run_tool_call; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_action
    ADD CONSTRAINT uq_assistant_action_run_tool_call UNIQUE (run_id, tool_call_id);


--
-- Name: assistant_tool_call uq_assistant_tool_call_idempotency; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_tool_call
    ADD CONSTRAINT uq_assistant_tool_call_idempotency UNIQUE (idempotency_key);


--
-- Name: assistant_tool_call uq_assistant_tool_call_run_call; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_tool_call
    ADD CONSTRAINT uq_assistant_tool_call_run_call UNIQUE (run_id, tool_call_id);


--
-- Name: audit_task uq_audit_task_agent_tool_call_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_task
    ADD CONSTRAINT uq_audit_task_agent_tool_call_id UNIQUE (agent_tool_call_id);


--
-- Name: regulation uq_regulation_agent_tool_call_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regulation
    ADD CONSTRAINT uq_regulation_agent_tool_call_id UNIQUE (agent_tool_call_id);


--
-- Name: regulation_chunk uq_regulation_chunk_index; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regulation_chunk
    ADD CONSTRAINT uq_regulation_chunk_index UNIQUE (regulation_id, chunk_index);


--
-- Name: regulation_parse_block uq_regulation_parse_block_index; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regulation_parse_block
    ADD CONSTRAINT uq_regulation_parse_block_index UNIQUE (regulation_id, block_index);


--
-- Name: ix_assistant_action_user_status_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assistant_action_user_status_expires ON public.assistant_action USING btree (user_id, status, expires_at);


--
-- Name: ix_assistant_agent_run_conversation_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assistant_agent_run_conversation_created ON public.assistant_agent_run USING btree (conversation_id, created_at);


--
-- Name: ix_assistant_agent_run_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assistant_agent_run_user_status ON public.assistant_agent_run USING btree (user_id, status);


--
-- Name: ix_assistant_conversation_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assistant_conversation_user_id ON public.assistant_conversation USING btree (user_id);


--
-- Name: ix_assistant_conversation_user_last_message_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assistant_conversation_user_last_message_id ON public.assistant_conversation USING btree (user_id, last_message_at, id);


--
-- Name: ix_assistant_message_conversation_created_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assistant_message_conversation_created_id ON public.assistant_message USING btree (conversation_id, created_at, id);


--
-- Name: ix_assistant_message_conversation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assistant_message_conversation_id ON public.assistant_message USING btree (conversation_id);


--
-- Name: ix_assistant_tool_call_run_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assistant_tool_call_run_created ON public.assistant_tool_call USING btree (run_id, created_at);


--
-- Name: ix_audit_task_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_task_document_id ON public.audit_task USING btree (document_id);


--
-- Name: ix_audit_task_page_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_task_page_task_id ON public.audit_task_page USING btree (task_id);


--
-- Name: ix_audit_task_page_task_page; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_task_page_task_page ON public.audit_task_page USING btree (task_id, page_number);


--
-- Name: ix_audit_task_page_task_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_task_page_task_status ON public.audit_task_page USING btree (task_id, status);


--
-- Name: ix_audit_task_page_timeout_scan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_task_page_timeout_scan ON public.audit_task_page USING btree (status, started_at, task_id);


--
-- Name: ix_audit_task_timeout_scan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_task_timeout_scan ON public.audit_task USING btree (status, stage, updated_at);


--
-- Name: ix_document_parse_block_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_parse_block_document_id ON public.document_parse_block USING btree (document_id);


--
-- Name: ix_document_parse_block_document_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_parse_block_document_index ON public.document_parse_block USING btree (document_id, block_index);


--
-- Name: ix_document_parse_block_document_page; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_parse_block_document_page ON public.document_parse_block USING btree (document_id, page_number);


--
-- Name: ix_document_parse_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_parse_task_id ON public.document USING btree (parse_task_id);


--
-- Name: ix_document_user_created_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_user_created_id ON public.document USING btree (user_id, created_at, id);


--
-- Name: ix_document_user_filename_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_user_filename_id ON public.document USING btree (user_id, original_filename, id);


--
-- Name: ix_document_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_user_id ON public.document USING btree (user_id);


--
-- Name: ix_document_user_size_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_user_size_id ON public.document USING btree (user_id, file_size, id);


--
-- Name: ix_document_user_status_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_document_user_status_id ON public.document USING btree (user_id, status, id);


--
-- Name: ix_evidence_document_block_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_evidence_document_block_id ON public.evidence USING btree (document_block_id);


--
-- Name: ix_finding_page_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finding_page_number ON public.finding USING btree (page_number);


--
-- Name: ix_finding_rule_reference_finding_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finding_rule_reference_finding_id ON public.finding_rule_reference USING btree (finding_id);


--
-- Name: ix_finding_rule_reference_regulation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finding_rule_reference_regulation_id ON public.finding_rule_reference USING btree (regulation_id);


--
-- Name: ix_finding_rule_reference_regulation_rule_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finding_rule_reference_regulation_rule_id ON public.finding_rule_reference USING btree (regulation_rule_id);


--
-- Name: ix_finding_task_page_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_finding_task_page_id ON public.finding USING btree (task_page_id);


--
-- Name: ix_operation_log_operation_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operation_log_operation_type ON public.operation_log USING btree (operation_type);


--
-- Name: ix_operation_log_parent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operation_log_parent_id ON public.operation_log USING btree (parent_id);


--
-- Name: ix_operation_log_request_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operation_log_request_id ON public.operation_log USING btree (request_id);


--
-- Name: ix_operation_log_target_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operation_log_target_id ON public.operation_log USING btree (target_id);


--
-- Name: ix_operation_log_target_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operation_log_target_type ON public.operation_log USING btree (target_type);


--
-- Name: ix_operation_log_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operation_log_user_id ON public.operation_log USING btree (user_id);


--
-- Name: ix_regulation_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_category ON public.regulation USING btree (category);


--
-- Name: ix_regulation_chunk_article_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_chunk_article_number ON public.regulation_chunk USING btree (article_number);


--
-- Name: ix_regulation_chunk_regulation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_chunk_regulation_id ON public.regulation_chunk USING btree (regulation_id);


--
-- Name: ix_regulation_document_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_document_number ON public.regulation USING btree (document_number);


--
-- Name: ix_regulation_effective_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_effective_date ON public.regulation USING btree (effective_date);


--
-- Name: ix_regulation_expiration_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_expiration_date ON public.regulation USING btree (expiration_date);


--
-- Name: ix_regulation_language; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_language ON public.regulation USING btree (language);


--
-- Name: ix_regulation_parse_block_regulation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_parse_block_regulation_id ON public.regulation_parse_block USING btree (regulation_id);


--
-- Name: ix_regulation_parse_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_parse_task_id ON public.regulation USING btree (parse_task_id);


--
-- Name: ix_regulation_rule_regulation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_rule_regulation_id ON public.regulation_rule USING btree (regulation_id);


--
-- Name: ix_regulation_rule_rule_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_rule_rule_type ON public.regulation_rule USING btree (rule_type);


--
-- Name: ix_regulation_rule_source_chunk_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_rule_source_chunk_id ON public.regulation_rule USING btree (source_chunk_id);


--
-- Name: ix_regulation_source_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_source_type ON public.regulation USING btree (source_type);


--
-- Name: ix_regulation_uploaded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_uploaded_by ON public.regulation USING btree (uploaded_by);


--
-- Name: ix_regulation_visibility; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_regulation_visibility ON public.regulation USING btree (visibility);


--
-- Name: uq_regulation_private_user_content_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_regulation_private_user_content_hash ON public.regulation USING btree (uploaded_by, content_hash) WHERE ((visibility)::text = 'PRIVATE'::text);


--
-- Name: uq_regulation_shared_content_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_regulation_shared_content_hash ON public.regulation USING btree (content_hash) WHERE ((visibility)::text = 'SHARED'::text);


--
-- Name: ux_app_user_username_lower; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_app_user_username_lower ON public.app_user USING btree (lower((username)::text));


--
-- Name: assistant_action assistant_action_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_action
    ADD CONSTRAINT assistant_action_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.assistant_conversation(id) ON DELETE CASCADE;


--
-- Name: assistant_action assistant_action_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_action
    ADD CONSTRAINT assistant_action_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.assistant_agent_run(id) ON DELETE CASCADE;


--
-- Name: assistant_agent_run assistant_agent_run_assistant_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_agent_run
    ADD CONSTRAINT assistant_agent_run_assistant_message_id_fkey FOREIGN KEY (assistant_message_id) REFERENCES public.assistant_message(id) ON DELETE CASCADE;


--
-- Name: assistant_agent_run assistant_agent_run_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_agent_run
    ADD CONSTRAINT assistant_agent_run_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.assistant_conversation(id) ON DELETE CASCADE;


--
-- Name: assistant_message assistant_message_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_message
    ADD CONSTRAINT assistant_message_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.assistant_conversation(id) ON DELETE CASCADE;


--
-- Name: assistant_tool_call assistant_tool_call_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_tool_call
    ADD CONSTRAINT assistant_tool_call_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.assistant_agent_run(id) ON DELETE CASCADE;


--
-- Name: audit_task audit_task_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_task
    ADD CONSTRAINT audit_task_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.document(id);


--
-- Name: audit_task_page audit_task_page_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_task_page
    ADD CONSTRAINT audit_task_page_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.audit_task(id);


--
-- Name: document_page document_page_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_page
    ADD CONSTRAINT document_page_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.document(id);


--
-- Name: document_parse_block document_parse_block_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_parse_block
    ADD CONSTRAINT document_parse_block_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.document(id);


--
-- Name: evidence evidence_finding_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evidence
    ADD CONSTRAINT evidence_finding_id_fkey FOREIGN KEY (finding_id) REFERENCES public.finding(id);


--
-- Name: finding_rule_reference finding_rule_reference_finding_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finding_rule_reference
    ADD CONSTRAINT finding_rule_reference_finding_id_fkey FOREIGN KEY (finding_id) REFERENCES public.finding(id);


--
-- Name: finding finding_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finding
    ADD CONSTRAINT finding_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.audit_task(id);


--
-- Name: audit_task fk_audit_task_agent_tool_call_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_task
    ADD CONSTRAINT fk_audit_task_agent_tool_call_id FOREIGN KEY (agent_tool_call_id) REFERENCES public.assistant_tool_call(id) ON DELETE SET NULL;


--
-- Name: finding fk_finding_task_page; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.finding
    ADD CONSTRAINT fk_finding_task_page FOREIGN KEY (task_page_id) REFERENCES public.audit_task_page(id);


--
-- Name: regulation fk_regulation_agent_tool_call_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regulation
    ADD CONSTRAINT fk_regulation_agent_tool_call_id FOREIGN KEY (agent_tool_call_id) REFERENCES public.assistant_tool_call(id) ON DELETE SET NULL;


--
-- Name: regulation_chunk regulation_chunk_regulation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regulation_chunk
    ADD CONSTRAINT regulation_chunk_regulation_id_fkey FOREIGN KEY (regulation_id) REFERENCES public.regulation(id) ON DELETE CASCADE;


--
-- Name: regulation_parse_block regulation_parse_block_regulation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regulation_parse_block
    ADD CONSTRAINT regulation_parse_block_regulation_id_fkey FOREIGN KEY (regulation_id) REFERENCES public.regulation(id) ON DELETE CASCADE;
