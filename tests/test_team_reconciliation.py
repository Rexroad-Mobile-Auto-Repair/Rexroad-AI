import json

import pytest

from app.autonomy.reconciliation import TeamReconciler
from app.autonomy.synthesis import SourceExcerpt, WorkerResultContract, synthesize
from app.config import Settings
from app.providers.models import ModelResponse
from app.providers.registry import ProviderRegistry


def workers():
    source = SourceExcerpt(path='example.py', text="return 'Hello'", audit_ref='trace-a')
    return [WorkerResultContract(worker_type='code_analyst', task_id='a', status='completed', summary='Returns Hello.', evidence_refs=['trace-a'], source_evidence=[source]),
            WorkerResultContract(worker_type='test_analyst', task_id='b', status='completed', summary='Returns Goodbye.', evidence_refs=['trace-b'], source_evidence=[source])]


def test_completed_workers_are_not_automatically_agreements():
    result = synthesize(workers())
    assert result.agreements == [] and result.requires_more_work
    assert result.reconciliation_status == 'pending'


def test_unavailable_saved_evidence_keeps_comparison_unresolved():
    def unavailable(scope, task_id):
        raise OSError('unavailable')

    results = [item.model_copy(update={'source_evidence': []}) for item in workers()]
    result = TeamReconciler(Settings(), ProviderRegistry(), unavailable).reconcile(results, 'chat:test')
    assert result.reconciliation_status == 'failed'
    assert result.requires_more_work
    assert 'could not be loaded' in result.unresolved_gaps[-1]


@pytest.mark.parametrize('task_ids,quote_text,expected', [(['a', 'b'], "return 'Hello'", 'completed'), (['a', 'invented'], "return 'Hello'", 'failed'), (['a'], "return 'Hello'", 'failed'), (['a', 'b'], "return 'Invented'", 'failed')])
def test_reconciliation_identifies_conflict_and_rejects_fabricated_evidence(task_ids, quote_text, expected):
    class Provider:
        name = 'openai_compatible'

        async def generate(self, request):
            assert request.tools == []
            assert 'trace-a' in request.messages[1].content
            quote = {'task_id': 'a', 'path': 'example.py', 'quote': quote_text}
            return ModelResponse(provider=self.name, model=request.model, content=json.dumps({
                'findings': [{'statement': 'Source returns Hello.', 'task_ids': ['a'], 'source_quotes': [quote]}],
                'disagreements': [{'statement': 'Workers disagree about the returned greeting.', 'task_ids': task_ids, 'source_quotes': [quote]}],
                'recommended_action': 'Recheck the source.',
            }))

    providers = ProviderRegistry()
    providers.register(Provider())
    result = TeamReconciler(Settings(), providers).reconcile(workers())
    assert result.reconciliation_status == expected
    assert result.requires_more_work is True
    if expected == 'completed':
        assert 'a, b' in result.disagreements[0]
    else:
        assert result.disagreements == [] and result.unresolved_gaps


def test_reconciliation_repairs_one_invalid_quote_without_running_workers():
    class Provider:
        name = 'openai_compatible'
        calls = 0

        async def generate(self, request):
            self.calls += 1
            assert request.tools == []
            if self.calls == 2:
                assert 'evidence validation error' in request.messages[-1].content
            quote = "return 'Invented'" if self.calls == 1 else "return 'Hello'"
            return ModelResponse(provider=self.name, model=request.model, content=json.dumps({
                'findings': [{'statement': 'Source returns Hello.', 'task_ids': ['a'],
                              'source_quotes': [{'task_id': 'a', 'path': 'example.py', 'quote': quote}]}],
                'recommended_action': 'No further source inspection needed.',
            }))

    provider = Provider()
    providers = ProviderRegistry()
    providers.register(provider)
    result = TeamReconciler(Settings(), providers).reconcile(workers())
    assert provider.calls == 2
    assert result.reconciliation_status == 'completed'
    assert not result.requires_more_work


@pytest.mark.parametrize('source_index,expected', [(0, 'completed'), (999, 'failed')])
def test_indexed_citations_resolve_to_audited_sources(source_index, expected):
    class Provider:
        name = 'openai_compatible'

        async def generate(self, request):
            return ModelResponse(provider=self.name, model=request.model, content=json.dumps({
                'findings': [{'statement': 'Source returns Hello.', 'task_ids':['a'], 'source_indices':[source_index]}],
                'recommended_action':'Review the source finding.'
            }))

    providers = ProviderRegistry()
    providers.register(Provider())
    result = TeamReconciler(Settings(), providers).reconcile(workers())
    assert result.reconciliation_status == expected
    assert result.requires_more_work == (expected == 'failed')
