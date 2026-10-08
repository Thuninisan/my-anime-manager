import unittest
from backend.domain.resource import resource_identity
from backend.domain.resource_adapters import provider_candidates
from backend.services.resource_resolver import ResourceResolver, unique_relation, resolve_primary_series_relation


class ResourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.resolver = ResourceResolver()

    def resolve(self, rows, **kw):
        return self.resolver.resolve(provider_candidates("tmdb", rows), **kw)

    def test_exact_normalized_and_order_independent(self):
        rows = [{"id": 1, "name": "other"}, {"id": 2, "name": "Ａ Show!"}]
        for ordered in (rows, rows[::-1]):
            r = self.resolve(ordered, title="a show")
            self.assertEqual(r["identity"]["tmdb_series_id"], 2)
            self.assertEqual(r["reason"], "exact_normalized_title")

    def test_ambiguous_and_empty(self):
        self.assertEqual(self.resolve([])["status"], "unresolved")
        self.assertEqual(self.resolve([{"id": 1}, {"id": 2}])["status"], "ambiguous")
        self.assertEqual(self.resolve([{"id": 1, "name": "A"}, {"id": 2, "name": "A"}], title="A")["status"], "ambiguous")

    def test_year(self):
        r = self.resolve([{"id": 1, "name": "A", "first_air_date": "2000-01-01"},
                          {"id": 2, "name": "A", "first_air_date": "2001-01-01"}], title="A", year=2001)
        self.assertEqual(r["identity"]["tmdb_series_id"], 2)

    def test_strong_ids_and_mapping(self):
        known = resource_identity(tmdb_series_id=9)
        mapping = resource_identity(tmdb_series_id=10)
        self.assertEqual(self.resolver.resolve([], known=known, existing_mapping=mapping)["identity"], known)
        self.assertEqual(self.resolver.resolve([], existing_mapping=mapping)["reason"], "existing_mapping")

    def test_single_fallback_is_explicit(self):
        self.assertEqual(self.resolve([{"id": 1}])["reason"], "legacy_single_candidate_fallback")
        self.assertEqual(self.resolve([{"id": 1}], allow_single_fallback=False)["status"], "unresolved")

    def test_media_and_validation(self):
        movie = provider_candidates("tmdb", [{"id": 1, "title": "A"}], "movie")
        self.assertEqual(self.resolver.resolve(movie)["status"], "unresolved")
        self.assertEqual(self.resolver.resolve(movie, media_type="movie")["identity"]["tmdb_movie_id"], 1)
        for kwargs in ({"tmdb_series_id": 0}, {"tmdb_series_id": True},
                       {"media_type": "movie", "tmdb_series_id": 1}, {"tmdb_movie_id": 1}):
            with self.assertRaises(ValueError):
                resource_identity(**kwargs)

    def test_relation_branches_and_order(self):
        rows = [{"id": 1, "relation": "番外篇"}, {"id": 2, "relation": "主线"},
                {"id": 3, "relation": "前传"}, {"id": 4, "relation": "续集"}]
        for ordered in (rows, rows[::-1]):
            self.assertEqual(resolve_primary_series_relation(ordered)["id"], 2)
            self.assertEqual(unique_relation(ordered, "前传")["id"], 3)
        self.assertIsNone(resolve_primary_series_relation(rows[:1]))
        with self.assertRaises(ValueError):
            unique_relation([{"id": 1, "relation": "前传"}, {"id": 2, "relation": "前传"}], "前传")

    def test_multi_series_independent(self):
        contexts = {name: self.resolve(rows, title=name)["identity"] for name, rows in {
            "A": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
            "B": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]}.items()}
        self.assertEqual(contexts["A"]["tmdb_series_id"], 1)
        self.assertEqual(contexts["B"]["tmdb_series_id"], 2)

    def test_special_keeps_subject_and_tv_binding(self):
        from backend.services.resource_resolver import resolve_special_binding
        parent = resource_identity(bangumi_subject_id=1, tmdb_series_id=10)
        special = resolve_special_binding(2, parent)
        self.assertEqual(special['identity']['bangumi_subject_id'], 2)
        self.assertEqual(special['identity']['tmdb_series_id'], 10)
        self.assertNotIn('season', special['identity'])
        self.assertEqual(special['reason'], 'bangumi_special_main_series_mapping')
        movie = resource_identity('movie', tmdb_movie_id=3)
        self.assertEqual(resolve_special_binding(2, movie)['status'], 'unresolved')

    def test_episode_link_ties_are_ambiguous(self):
        from backend.services.resource_resolver import resolve_episode_link_scores
        for scores in ({1: .9, 2: .9}, {2: .9, 1: .9}):
            self.assertEqual(resolve_episode_link_scores('tmdb', scores)['status'], 'ambiguous')
        resolved = resolve_episode_link_scores('tvdb', {1: .7, 2: .9})
        self.assertEqual(resolved['identity']['tvdb_series_id'], 2)
        self.assertEqual(resolved['reason'], 'legacy_episode_name_link')

    def test_batch_contexts_keep_independent_bindings(self):
        from backend.services.batch_episode_mapper import normalize_batch_episode
        for provider_id in (10, 20):
            mapping = normalize_batch_episode({'tmdb_id': provider_id, 'tmdb_season': 0, 'tmdb_episode': 1})
            self.assertEqual(mapping['tmdb']['series_id'], provider_id)
        with self.assertRaises(ValueError):
            normalize_batch_episode({'tmdb_id': 10, 'resource_identity': resource_identity(tmdb_series_id=20)})

    def test_subscription_edits_win_over_stale_identity(self):
        from backend.domain.resource_adapters import subscription_identity
        sub = {'tmdb': {'id': 20}, 'resource_identity': resource_identity(tmdb_series_id=10)}
        self.assertEqual(subscription_identity(sub, 5)['tmdb_series_id'], 20)
