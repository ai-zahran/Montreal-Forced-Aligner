"""Classes and functions for working with TextGrids in MFA"""
from __future__ import annotations

import os
import re
import sys
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Union

from praatio import textgrid as tgio
from praatio.utilities.textgrid_io import Interval

if TYPE_CHECKING:
    from .aligner.base import BaseAligner
    from .corpus.classes import DictionaryData, File, Speaker
    from .dictionary import (
        DictionaryEntryType,
        IpaType,
        MappingType,
        PunctuationType,
        ReversedMappingType,
    )
    from .multiprocessing.alignment import CtmType
    from .trainers.base import BaseTrainer

    AlignerType = Union[BaseTrainer, BaseAligner]

__all__ = [
    "CtmInterval",
    "process_ctm_line",
    "map_to_original_pronunciation",
    "parse_from_word",
    "parse_from_phone",
    "parse_from_word_no_cleanup",
    "generate_tiers",
    "export_textgrid",
    "ctm_to_textgrid",
    "output_textgrid_writing_errors",
    "to_int",
    "ctms_to_textgrids_non_mp",
    "split_clitics",
]


@dataclass
class CtmInterval:
    """
    Data class for intervals derived from CTM files

    Attributes
    ----------
    begin: float
        Start time of interval
    end: float
        End time of interval
    label: str
        Text of interval
    utterance: str
        Utterance ID that the interval belongs to
    """

    begin: float
    end: float
    label: str
    utterance: str

    def shift_times(self, offset: float):
        """
        Shift times of the interval based on some offset (i.e., segments in Kaldi)

        Parameters
        ----------
        offset: float
            Offset to add to the interval's begin and end

        """
        self.begin += offset
        self.end += offset

    def to_tg_interval(self) -> Interval:
        """
        Converts the CTMInterval to PraatIO's Interval class

        Returns
        -------
        Interval
            Derived PraatIO Interval
        """
        return Interval(self.begin, self.end, self.label)


def process_ctm_line(line: str) -> CtmInterval:
    """
    Helper function for parsing a line of CTM file to construct a CTMInterval

    Parameters
    ----------
    line: str
        Input string

    Returns
    -------
    CtmInterval
        Extracted data from the line
    """
    line = line.split(" ")
    utt = line[0]
    begin = round(float(line[2]), 4)
    duration = float(line[3])
    end = round(begin + duration, 4)
    label = line[4]
    return CtmInterval(begin, end, label, utt)


def split_clitics(
    item: str,
    words_mapping: MappingType,
    clitic_set: Set[str],
    clitic_markers: PunctuationType,
    compound_markers: PunctuationType,
) -> List[str]:
    """
    Split a word into subwords based on dictionary information

    Parameters
    ----------
    item: str
        Word to split
    words_mapping: Dict[str, int]
        A word mapping from a Dictionary object
    clitic_set: Set[str]
        Set of acceptable clitics from the dictionary
    clitic_markers: str
        Clitic markers
    compound_markers: str
        Compound markers

    Returns
    -------
    List[str]
        List of subwords
    """
    if item in words_mapping:
        return [item]
    if any(x in item for x in compound_markers):
        s = re.split(rf"[{compound_markers}]", item)
        if any(x in item for x in clitic_markers):
            new_s = []
            for seg in s:
                if any(x in seg for x in clitic_markers):
                    new_s.extend(
                        split_clitics(
                            seg, words_mapping, clitic_set, clitic_markers, compound_markers
                        )
                    )
                else:
                    new_s.append(seg)
            s = new_s
        return s
    if any(x in item and not item.endswith(x) and not item.startswith(x) for x in clitic_markers):
        initial, final = re.split(rf"[{clitic_markers}]", item, maxsplit=1)
        if any(x in final for x in clitic_markers):
            final = split_clitics(
                final, words_mapping, clitic_set, clitic_markers, compound_markers
            )
        else:
            final = [final]
        for clitic in clitic_markers:
            if initial + clitic in clitic_set:
                return [initial + clitic] + final
            elif clitic + final[0] in clitic_set:
                final[0] = clitic + final[0]
                return [initial] + final
    return [item]


def _lookup(
    item: str,
    words_mapping: MappingType,
    punctuation: PunctuationType,
    clitic_set: Set[str],
    clitic_markers: PunctuationType,
    compound_markers: PunctuationType,
) -> List[str]:
    """
    Look up a word and return the list of sub words if necessary
    taking into account clitic and compound markers

    Parameters
    ----------
    item: str
        Word to look up

    Returns
    -------
    List[str]
        List of subwords that are in the dictionary
    """
    from montreal_forced_aligner.dictionary import sanitize

    if item in words_mapping:
        return [item]
    sanitized = sanitize(item, punctuation, clitic_markers)
    if sanitized in words_mapping:
        return [sanitized]
    split = split_clitics(sanitized, words_mapping, clitic_set, clitic_markers, compound_markers)
    oov_count = sum(1 for x in split if x not in words_mapping)

    if oov_count < len(split):  # Only returned split item if it gains us any transcribed speech
        return split
    return [sanitized]


def to_int(
    item: str,
    words_mapping: MappingType,
    punctuation: PunctuationType,
    clitic_set: Set[str],
    clitic_markers: PunctuationType,
    compound_markers: PunctuationType,
    oov_int: int,
) -> List[int]:
    """
    Convert a given word into integer IDs

    Parameters
    ----------
    item: str
        Word to look up

    Returns
    -------
    List[int]
        List of integer IDs corresponding to each subword
    """
    if item == "":
        return []
    sanitized = _lookup(
        item, words_mapping, punctuation, clitic_set, clitic_markers, compound_markers
    )
    text_int = []
    for item in sanitized:
        if not item:
            continue
        if item not in words_mapping:
            text_int.append(oov_int)
        else:
            text_int.append(words_mapping[item])
    return text_int


def parse_from_word(
    ctm_labels: List[CtmInterval], text: List[str], dictionary_data: DictionaryData
) -> List[CtmInterval]:
    """
    Parse CTM intervals into the corresponding text for an utterance

    Parameters
    ----------
    ctm_labels: List[CtmInterval]
        CTM intervals
    text: List[str]
        The original text that was to be aligned
    dictionary_data: DictionaryData
        Dictionary data necessary for splitting subwords

    Returns
    -------
    List[CtmInterval]
        Correct intervals with subwords merged back into their original text
    """
    cur_ind = 0
    actual_labels = []
    utterance = None
    words_mapping = dictionary_data.words_mapping
    punctuation = dictionary_data.punctuation
    clitic_set = dictionary_data.clitic_set
    clitic_markers = dictionary_data.clitic_markers
    compound_markers = dictionary_data.compound_markers
    oov_int = dictionary_data.oov_int
    for word in text:
        ints = to_int(
            word, words_mapping, punctuation, clitic_set, clitic_markers, compound_markers, oov_int
        )
        b = 1000000
        e = -1
        for i in ints:
            cur = ctm_labels[cur_ind]
            if utterance is None:
                utterance = cur.utterance
            if i == int(cur.label):
                if cur.begin < b:
                    b = cur.begin
                if cur.end > e:
                    e = cur.end
            cur_ind += 1
        lab = CtmInterval(b, e, word, utterance)
        actual_labels.append(lab)
    return actual_labels


def parse_from_word_no_cleanup(
    ctm_labels: List[CtmInterval], reversed_word_mapping: ReversedMappingType
) -> List[CtmInterval]:
    """
    Assume that subwords in the CTM files are desired, so just does a reverse look up to get the sub word
    text

    Parameters
    ----------
    ctm_labels: List[CtmInterval]
        List of CtmIntervals to convert
    reversed_word_mapping: Dict[int, str]
        Look up for Kaldi word IDs to convert them back to text

    Returns
    -------
    List[CtmInterval]
        Parsed intervals with text rather than integer IDs
    """
    for ctm_interval in ctm_labels:
        label = reversed_word_mapping[int(ctm_interval.label)]
        ctm_interval.label = label
    return ctm_labels


def parse_from_phone(
    ctm_labels: List[CtmInterval],
    reversed_phone_mapping: ReversedMappingType,
    positions: List[str],
) -> List[CtmInterval]:
    """
    Parse CtmIntervals to original phone transcriptions

    Parameters
    ----------
    ctm_labels: List[CtmInterval]
        CtmIntervals to convert
    reversed_phone_mapping: Dict[int, str]
        Mapping to convert phone IDs to phone labels
    positions: List[str]
        List of word positions to account for

    Returns
    -------
    List[CtmInterval]
        Parsed intervals with phone labels rather than IDs
    """
    for ctm_interval in ctm_labels:
        label = reversed_phone_mapping[int(ctm_interval.label)]
        for p in positions:
            if label.endswith(p):
                label = label[: -1 * len(p)]
        ctm_interval.label = label
    return ctm_labels


def map_to_original_pronunciation(
    phones: CtmType, subpronunciations: List[DictionaryEntryType], strip_diacritics: IpaType
) -> CtmType:
    """
    Convert phone transcriptions from multilingual IPA mode to their original IPA transcription

    Parameters
    ----------
    phones: List[CtmInterval]
        List of aligned phones
    subpronunciations: List[DictionaryEntryType]
        Pronunciations of each sub word to reconstruct the transcriptions
    strip_diacritics: List[str]
        List of diacritics that were stripped out of the original IPA transcription

    Returns
    -------
    List[CtmInterval]
        Intervals with their original IPA pronunciation rather than the internal simplified form
    """
    transcription = tuple(x.label for x in phones)
    new_phones = []
    mapping_ind = 0
    transcription_ind = 0
    for pronunciations in subpronunciations:
        pron = None
        if mapping_ind >= len(phones):
            break
        for p in pronunciations:
            if (
                "original_pronunciation" in p
                and transcription == p["pronunciation"] == p["original_pronunciation"]
            ) or (transcription == p["pronunciation"] and "original_pronunciation" not in p):
                new_phones.extend(phones)
                mapping_ind += len(phones)
                break
            if (
                p["pronunciation"]
                == transcription[transcription_ind : transcription_ind + len(p["pronunciation"])]
                and pron is None
            ):
                pron = p
        if mapping_ind >= len(phones):
            break
        if not pron:
            new_phones.extend(phones)
            mapping_ind += len(phones)
            break
        to_extend = phones[transcription_ind : transcription_ind + len(pron["pronunciation"])]
        transcription_ind += len(pron["pronunciation"])
        p = pron
        if "original_pronunciation" not in p or p["pronunciation"] == p["original_pronunciation"]:

            new_phones.extend(to_extend)
            mapping_ind += len(to_extend)
            break
        for pi in p["original_pronunciation"]:
            if pi == phones[mapping_ind].label:
                new_phones.append(phones[mapping_ind])
            else:
                modded_phone = pi
                new_p = phones[mapping_ind].label
                for diacritic in strip_diacritics:
                    modded_phone = modded_phone.replace(diacritic, "")
                if modded_phone == new_p:
                    phones[mapping_ind].label = pi
                    new_phones.append(phones[mapping_ind])
                elif mapping_ind != len(phones) - 1:
                    new_p = phones[mapping_ind].label + phones[mapping_ind + 1].label
                    if modded_phone == new_p:
                        new_phones.append(
                            CtmInterval(
                                phones[mapping_ind].begin,
                                phones[mapping_ind + 1].end,
                                new_p,
                                phones[mapping_ind].utterance,
                            )
                        )
                        mapping_ind += 1
            mapping_ind += 1
    return new_phones


def ctms_to_textgrids_non_mp(aligner: AlignerType) -> None:
    """
    Parse CTM files to TextGrids without using multiprocessing

    Parameters
    ----------
    aligner: AlignerType
        Aligner that generated the CTM files
    """

    def process_current_word_labels():
        """Process the current stack of word labels"""
        speaker = cur_utt.speaker

        text = cur_utt.text.split()
        if aligner.align_config.cleanup_textgrids:
            actual_labels = parse_from_word(current_labels, text, speaker.dictionary_data)
        else:
            actual_labels = parse_from_word_no_cleanup(
                current_labels, speaker.dictionary_data.reversed_words_mapping
            )
        cur_utt.word_labels = actual_labels

    def process_current_phone_labels():
        """Process the current stack of phone labels"""
        speaker = cur_utt.speaker

        cur_utt.phone_labels = parse_from_phone(
            current_labels, speaker.dictionary.reversed_phone_mapping, speaker.dictionary.positions
        )

    export_errors = {}
    for j in aligner.corpus.jobs:

        word_arguments = j.cleanup_word_ctm_arguments(aligner)
        phone_arguments = j.phone_ctm_arguments(aligner)
        aligner.logger.debug(f"Parsing ctms for job {j.name}...")
        cur_utt = None
        current_labels = []
        for dict_name in word_arguments.dictionaries:
            with open(word_arguments.ctm_paths[dict_name], "r") as f:
                for line in f:
                    line = line.strip()
                    if line == "":
                        continue
                    ctm_interval = process_ctm_line(line)
                    utt = aligner.corpus.utterances[ctm_interval.utterance]
                    if cur_utt is None:
                        cur_utt = utt
                    if utt.is_segment:
                        utt_begin = utt.begin
                    else:
                        utt_begin = 0
                    if utt != cur_utt:
                        process_current_word_labels()
                        cur_utt = utt
                        current_labels = []

                    ctm_interval.shift_times(utt_begin)
                    current_labels.append(ctm_interval)
            if current_labels:
                process_current_word_labels()
        cur_utt = None
        current_labels = []
        for dict_name in phone_arguments.dictionaries:
            with open(phone_arguments.ctm_paths[dict_name], "r") as f:
                for line in f:
                    line = line.strip()
                    if line == "":
                        continue
                    ctm_interval = process_ctm_line(line)
                    utt = aligner.corpus.utterances[ctm_interval.utterance]
                    if cur_utt is None:
                        cur_utt = utt
                    if utt.is_segment:
                        utt_begin = utt.begin
                    else:
                        utt_begin = 0
                    if utt != cur_utt and cur_utt is not None:
                        process_current_phone_labels()
                        cur_utt = utt
                        current_labels = []

                    ctm_interval.shift_times(utt_begin)
                    current_labels.append(ctm_interval)
            if current_labels:
                process_current_phone_labels()

        aligner.logger.debug(f"Generating TextGrids for job {j.name}...")
        processed_files = set()
        for file in j.job_files().values():
            first_file_write = True
            if file.name in processed_files:
                first_file_write = False
            try:
                ctm_to_textgrid(file, aligner, first_file_write)
                processed_files.add(file.name)
            except Exception:
                if aligner.align_config.debug:
                    raise
                exc_type, exc_value, exc_traceback = sys.exc_info()
                export_errors[file.name] = "\n".join(
                    traceback.format_exception(exc_type, exc_value, exc_traceback)
                )
    if export_errors:
        aligner.logger.warning(
            f"There were {len(export_errors)} errors encountered in generating TextGrids. "
            f"Check the output_errors.txt file in {os.path.join(aligner.textgrid_output)} "
            f"for more details"
        )
    output_textgrid_writing_errors(aligner.textgrid_output, export_errors)


def output_textgrid_writing_errors(output_directory: str, export_errors: Dict[str, str]) -> None:
    """
    Output any errors that were encountered in writing TextGrids

    Parameters
    ----------
    output_directory: str
        Directory to save TextGrids files
    export_errors: Dict[str, str]
        Dictionary of errors encountered
    """
    error_log = os.path.join(output_directory, "output_errors.txt")
    if os.path.exists(error_log):
        os.remove(error_log)
    for file_name, result in export_errors.items():
        if not os.path.exists(error_log):
            with open(error_log, "w", encoding="utf8") as f:
                f.write(
                    "The following exceptions were encountered during the output of the alignments to TextGrids:\n\n"
                )
        with open(error_log, "a", encoding="utf8") as f:
            f.write(f"{file_name}:\n")
            f.write(f"{result}\n\n")


def generate_tiers(
    file: File, cleanup_textgrids: Optional[bool] = True
) -> Dict[Speaker, Dict[str, CtmType]]:
    """
    Generate TextGrid tiers for a given File

    Parameters
    ----------
    file: File
        File to generate TextGrid tiers
    cleanup_textgrids: bool, optional
        Flag for whether the TextGrids should be cleaned up

    Returns
    -------
    Dict[Speaker, Dict[str, CtmType]]
        Tier information per speaker, with CtmIntervals split by "phones" and "words"
    """
    output = {}

    for u in file.utterances.values():
        if not u.word_labels:
            continue
        speaker = u.speaker
        dictionary_data = speaker.dictionary_data

        words = []
        phones = []
        if dictionary_data.multilingual_ipa and cleanup_textgrids:
            phone_ind = 0
            for interval in u.word_labels:
                end = interval.end
                word = interval.label
                subwords = _lookup(
                    word,
                    dictionary_data.words_mapping,
                    dictionary_data.punctuation,
                    dictionary_data.clitic_set,
                    dictionary_data.clitic_markers,
                    dictionary_data.compound_markers,
                )
                subwords = [
                    x if x in dictionary_data.words_mapping else dictionary_data.oov_code
                    for x in subwords
                ]
                subprons = [dictionary_data.words[x] for x in subwords]
                cur_phones = []
                while u.phone_labels[phone_ind].end <= end:
                    p = u.phone_labels[phone_ind]
                    if p.label in dictionary_data.silences:
                        phone_ind += 1
                        continue
                    cur_phones.append(p)
                    phone_ind += 1
                    if phone_ind > len(u.phone_labels) - 1:
                        break
                phones.extend(
                    map_to_original_pronunciation(
                        cur_phones, subprons, dictionary_data.strip_diacritics
                    )
                )
                if not word:
                    continue

                words.append(interval)
        else:
            for interval in u.word_labels:
                words.append(interval)
            for interval in u.phone_labels:
                if interval.label in dictionary_data.silences and cleanup_textgrids:
                    continue
                phones.append(interval)
        if speaker not in output:
            output[speaker] = {"words": words, "phones": phones}
        else:
            output[speaker]["words"].extend(words)
            output[speaker]["phones"].extend(phones)
    return output


def export_textgrid(
    file: File,
    output_path: str,
    speaker_data: Dict[Speaker, Dict[str, CtmType]],
    frame_shift: int,
    first_file_write: Optional[bool] = True,
) -> None:
    """
    Export aligned file to TextGrid

    Parameters
    ----------
    file: File
        File object to export
    output_path: str
        Output path of the file
    speaker_data: Dict[Speaker, Dict[str, CtmType]]
        Per speaker, per word/phone CtmIntervals
    frame_shift: int
        Frame shift of features, in ms
    first_file_write: bool, optional
        Flag for whether the file should be created from scratch or appended to if it
        has been modified by another export process
    """
    if frame_shift > 1:
        frame_shift = round(frame_shift / 1000, 4)
    if first_file_write:
        # Create initial textgrid
        tg = tgio.Textgrid()
        tg.minTimestamp = 0
        tg.maxTimestamp = file.duration

        if len(file.speaker_ordering) > 1:
            for speaker in file.speaker_ordering:
                word_tier_name = f"{speaker} - words"
                phone_tier_name = f"{speaker} - phones"

                word_tier = tgio.IntervalTier(word_tier_name, [], minT=0, maxT=file.duration)
                phone_tier = tgio.IntervalTier(phone_tier_name, [], minT=0, maxT=file.duration)
                tg.addTier(word_tier)
                tg.addTier(phone_tier)
        else:
            word_tier_name = "words"
            phone_tier_name = "phones"
            word_tier = tgio.IntervalTier(word_tier_name, [], minT=0, maxT=file.duration)
            phone_tier = tgio.IntervalTier(phone_tier_name, [], minT=0, maxT=file.duration)
            tg.addTier(word_tier)
            tg.addTier(phone_tier)
    else:
        # Use existing
        tg = tgio.openTextgrid(output_path, includeEmptyIntervals=False)

        word_tier_name = "words"
        phone_tier_name = "phones"
        word_tier = tgio.IntervalTier(word_tier_name, [], minT=0, maxT=file.duration)
        phone_tier = tgio.IntervalTier(phone_tier_name, [], minT=0, maxT=file.duration)
        tg.addTier(word_tier)
        tg.addTier(phone_tier)
    for speaker, data in speaker_data.items():
        words = data["words"]
        phones = data["phones"]
        tg_words = []
        tg_phones = []
        for w in words:
            if file.duration - w.end < frame_shift:  # Fix rounding issues
                w.end = file.duration
            tg_words.append(w.to_tg_interval())
        for p in phones:
            if file.duration - p.end < frame_shift:  # Fix rounding issues
                p.end = file.duration
            tg_phones.append(p.to_tg_interval())

        if len(file.speaker_ordering) > 1:
            word_tier_name = f"{speaker} - words"
            phone_tier_name = f"{speaker} - phones"
        else:
            word_tier_name = "words"
            phone_tier_name = "phones"
        word_tier = tgio.IntervalTier(word_tier_name, tg_words, minT=0, maxT=file.duration)
        phone_tier = tgio.IntervalTier(phone_tier_name, tg_phones, minT=0, maxT=file.duration)
        tg.replaceTier(word_tier_name, word_tier)
        tg.replaceTier(phone_tier_name, phone_tier)

    tg.save(output_path, includeBlankSpaces=True, format="long_textgrid", reportingMode="error")


def ctm_to_textgrid(file: File, aligner: AlignerType, first_file_write=True) -> None:
    """
    Export a File to TextGrid

    Parameters
    ----------
    file: File
        File to export
    aligner: AlignerType
        Aligner used to generate the alignments
    first_file_write: bool, optional
        Flag for whether this is the first time touching this file
    """
    data = generate_tiers(file, cleanup_textgrids=aligner.align_config.cleanup_textgrids)

    backup_output_directory = None
    if not aligner.align_config.overwrite:
        backup_output_directory = aligner.backup_output_directory
        os.makedirs(backup_output_directory, exist_ok=True)
    output_path = file.construct_output_path(aligner.textgrid_output, backup_output_directory)
    export_textgrid(
        file, output_path, data, aligner.align_config.feature_config.frame_shift, first_file_write
    )
