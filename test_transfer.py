import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from transfer_playlist import read_tracks, score, match_tracks, upload


class TransferTests(unittest.TestCase):
    def test_large_csv_has_no_500_cap(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'tracks.csv'
            p.write_text('Track Name,Artist Name(s)\n' + 'Awake,Tycho\n' * 601)
            self.assertEqual(len(read_tracks(p)), 601)

    def test_original_match_rejects_remix_and_wrong_artist(self):
        source = {'title': 'Awake', 'artist': 'Tycho', 'duration_ms': '284000'}
        candidate = {'title': 'Awake', 'artists': [{'name': 'Tycho'}], 'duration_seconds': 284}
        self.assertEqual(score(source, candidate), 1)
        self.assertEqual(score(source, {**candidate, 'title': 'Awake Remix'}), 0)
        self.assertEqual(score(source, {**candidate, 'artists': [{'name': 'Other'}]}), 0)
        self.assertEqual(score(source, {**candidate, 'duration_seconds': 200}), 0)

    def test_match_resume_preserves_approval_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as d, patch('transfer_playlist.time.sleep'):
            source, report = Path(d) / 'in.csv', Path(d) / 'review.json'
            source.write_text('title,artist\nAwake,Tycho\n')
            client = MagicMock()
            client.search.return_value = [{'videoId': 'dm4tkSNKfFI', 'title': 'Awake', 'artists': [{'name': 'Tycho'}]}]
            data = match_tracks(source, report, client)
            self.assertFalse(data['matches'][0]['approved'])
            data['matches'][0]['approved'] = True
            report.write_text(json.dumps(data))
            self.assertTrue(match_tracks(source, report, client)['matches'][0]['approved'])
            self.assertEqual(client.search.call_count, 1)
            source.write_text('title,artist\nOther,Other\n')
            with self.assertRaises(ValueError): match_tracks(source, report, client)

    def test_upload_batches_and_resumes_without_recreating(self):
        with tempfile.TemporaryDirectory() as d, patch('transfer_playlist.time.sleep'):
            report, state = Path(d) / 'review.json', Path(d) / 'state.json'
            ids = [f'{i:011d}' for i in range(121)]
            report.write_text(json.dumps({'matches': [{'video_id': v, 'approved': True} for v in ids] + [{'video_id': 'abcdefghijk', 'approved': False}]}))
            client = MagicMock()
            client.create_playlist.return_value = 'PL1234567890'
            client.get_playlist.return_value = {'tracks': [{'videoId': ids[0]}], 'trackCount': 1}
            client.add_playlist_items.return_value = {'status': 'STATUS_SUCCEEDED'}
            upload(report, state, 'Test', client)
            self.assertEqual([len(c.args[1]) for c in client.add_playlist_items.call_args_list], [50, 50, 20])
            client.get_playlist.return_value = {'tracks': [{'videoId': v} for v in ids], 'trackCount': 121}
            client.add_playlist_items.reset_mock()
            upload(report, state, 'Test', client)
            self.assertEqual(client.create_playlist.call_count, 1)
            client.add_playlist_items.assert_not_called()
            state.write_text('{"creating": true}')
            with self.assertRaises(RuntimeError): upload(report, state, 'Test', client)


if __name__ == '__main__': unittest.main()
