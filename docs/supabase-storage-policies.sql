-- TestExchange private evidence Storage setup
--
-- Run this file in the Supabase SQL Editor if `alembic upgrade head` reports that the
-- connection role is not the owner of storage.objects. Supabase manages that table with
-- an internal role; the SQL Editor has the project-level privileges needed to create policies.

create schema if not exists private;
revoke all on schema private from public;
grant usage on schema private to authenticated;

create or replace function private.testexchange_can_read_evidence(folder text)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
    return exists (
        select 1
        from public.assignments as assignment
        join public.campaigns as campaign on campaign.id = assignment.campaign_id
        where assignment.id = folder::uuid
          and (
              assignment.tester_id = auth.uid()
              or campaign.owner_id = auth.uid()
          )
    );
exception when invalid_text_representation then
    return false;
end;
$$;

create or replace function private.testexchange_can_upload_evidence(folder text)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
    return exists (
        select 1
        from public.assignments as assignment
        where assignment.id = folder::uuid
          and assignment.tester_id = auth.uid()
          and assignment.status in ('accepted', 'in_progress', 'changes_requested')
    );
exception when invalid_text_representation then
    return false;
end;
$$;

revoke all on function private.testexchange_can_read_evidence(text) from public;
revoke all on function private.testexchange_can_upload_evidence(text) from public;
grant execute on function private.testexchange_can_read_evidence(text) to authenticated;
grant execute on function private.testexchange_can_upload_evidence(text) to authenticated;

insert into storage.buckets (
    id, name, public, file_size_limit, allowed_mime_types
) values (
    'test-evidence',
    'test-evidence',
    false,
    52428800,
    array[
        'image/png', 'image/jpeg', 'image/webp', 'video/mp4',
        'text/plain', 'application/pdf', 'application/zip'
    ]
)
on conflict (id) do update set
    public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists testexchange_evidence_read on storage.objects;
drop policy if exists testexchange_evidence_upload on storage.objects;

create policy testexchange_evidence_read
on storage.objects for select to authenticated
using (
    bucket_id = 'test-evidence'
    and private.testexchange_can_read_evidence((storage.foldername(name))[1])
);

create policy testexchange_evidence_upload
on storage.objects for insert to authenticated
with check (
    bucket_id = 'test-evidence'
    and private.testexchange_can_upload_evidence((storage.foldername(name))[1])
);

-- There are intentionally no UPDATE or DELETE policies. Evidence is immutable from the client.
