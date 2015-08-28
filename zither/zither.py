
##   Copyright 2014 Bioinformatics Core, University of Michigan
##
##   Licensed under the Apache License, Version 2.0 (the "License");
##   you may not use this file except in compliance with the License.
##   You may obtain a copy of the License at
##
##       http://www.apache.org/licenses/LICENSE-2.0
##
##   Unless required by applicable law or agreed to in writing, software
##   distributed under the License is distributed on an "AS IS" BASIS,
##   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
##   See the License for the specific language governing permissions and
##   limitations under the License.

#pylint: disable=invalid-name, too-few-public-methods, too-many-locals
from __future__ import print_function, absolute_import, division
import argparse
import csv
from collections import OrderedDict
from zither import __version__
from datetime import datetime
import os.path
import pysam
import sys

_VCF_FIXED_HEADERS = ["#CHROM",
                      "POS",
                      "ID",
                      "REF",
                      "ALT",
                      "QUAL",
                      "FILTER",
                      "INFO",
                      "FORMAT"]

_NULL = "."

class _ExplicitBamFileStrategy(object):
    def __init__(self, bam_file_path):
        self._bam_file_path = bam_file_path

    def build_sample_bam_mapping(self):
        sample_file = os.path.basename(self._bam_file_path)
        sample_name = os.path.splitext(sample_file)[0]
        return {sample_name: self._bam_file_path}


class _MappingFileStrategy(object):
    def __init__(self, mapping_file):
        self._mapping_file = mapping_file

    def _abs_path(self, path):
        mapping_dir_path = os.path.dirname(self._mapping_file)
        if path != os.path.abspath(path):
            path = os.path.abspath(os.path.join(mapping_dir_path, path))
        return path

    def build_sample_bam_mapping(self):
        sample_bam_mapping = OrderedDict()
        with open(self._mapping_file, 'rb') as tsvfile:
            for sample_name, bam_path in csv.reader(tsvfile, delimiter='\t'):
                sample_bam_mapping[sample_name] = self._abs_path(bam_path)
        return sample_bam_mapping

class _MatchingNameStrategy(object):
    def __init__(self, sample_names, input_vcf_path):
        self._sample_names = sample_names
        self._input_vcf_path = input_vcf_path

    def build_sample_bam_mapping(self):
        sample_bam_mapping = OrderedDict()
        bam_dir = os.path.dirname(self._input_vcf_path)
        for sample_name in self._sample_names:
            bam_path = os.path.join(bam_dir, sample_name + ".bam")
            sample_bam_mapping[sample_name] = bam_path
        return sample_bam_mapping


def _get_sample_bam_strategy(args):
    if args.mapping_file:
        return _MappingFileStrategy(args.mapping_file)
    elif args.bam:
        return _ExplicitBamFileStrategy(args.bam)
    else:
        sample_names = _get_sample_names(args.input_vcf)
        return _MatchingNameStrategy(sample_names, args.input_vcf)

        
class _PileupStats(object):
    _PYSAM_BASE_INDEX = {'A':0, 'C':1, 'G':2, 'T':3}
    def __init__(self, ref, alt, unfiltered_coverage, filtered_coverage):
        (self.total_depth, self.total_af) = self._init_depth_freq(ref, alt, unfiltered_coverage)
        (self.zither_total_depth, self.zither_total_af) = self._init_depth_freq(ref, alt, unfiltered_coverage)
        (self.filtered_depth, self.filtered_af) = self._init_depth_freq(ref, alt, filtered_coverage)
        
    def _init_depth_freq(self, ref, alt, coverage):
        alt = alt.upper()
        freq = _NULL
        total_depth = (coverage[0][0] +
                            coverage[1][0] +
                            coverage[2][0] +
                            coverage[3][0])
        # zither_total_depth = ()
        # filtered_depth = ()
        try:
            variant_count = coverage[self._PYSAM_BASE_INDEX[alt]][0]
            if total_depth and len(ref)==1:
                freq = str(variant_count/total_depth)
            #elif zither_total_depth and len(ref)==1:
                #freq = str(variant_count/zither_total_depth)
            #elif filtered_depth and len(ref)==1:
                #freq = str(variant_count/filtered_depth)
        except KeyError:
            freq = _NULL
        return (total_depth, freq)

        
class _BamReader(object):
    def __init__(self, bam_file_name):
        self._bam_file_name = bam_file_name
        #pylint: disable=no-member
        self._bam_file = pysam.AlignmentFile(bam_file_name, "rb")

    def __eq__(self, other):
        return (isinstance(other,_BamReader) and
                self._bam_file_name == other._bam_file_name)

    def __hash__(self):
        return hash(self._bam_file_name)

    def get_pileup_stats(self, chrom, pos_one_based, ref, alt):
        pos_zero_based = pos_one_based - 1
        try:
            coverage = self._bam_file.count_coverage(chr=chrom,
                                                     start=pos_zero_based,
                                                     stop=pos_one_based,
                                                     quality_threshold=-1,
                                                     read_callback='nofilter')
        except ValueError as samtools_error:
            if str(samtools_error).startswith("invalid reference"):
                coverage = [[0], [0], [0], [0]]
            else:
                raise samtools_error
        return _PileupStats(ref, alt, coverage, coverage)

class _Tag(object):
    _METAHEADER = '##FORMAT=<ID={},Number={},Type={},Description="{}">'
    def __init__(self, id, number, type, description, stats_method):
        self.metaheader = self._METAHEADER.format(id, number, type, description)
        self.id = id
        self._get_value_method = stats_method
    
    def get_value(self, pileup_stats):
        return str(self._get_value_method(pileup_stats)) 


zither_total_depth = _Tag("ZTDP", "1", "Integer", "Zither total (unfiltered) BAM depth", lambda pileup_stats: pileup_stats.zither_total_depth) 
zither_total_af = _Tag("ZTAF", "1", "Float", "Zither total (unfiltered) BAM alt frequency", lambda pileup_stats: pileup_stats.zither_total_af)
filtered_depth = _Tag("ZFDP", "1", "Integer", "Zither filtered BAM depth", lambda pileup_stats: pileup_stats.filtered_depth)
filtered_af = _Tag("ZFAF", "1", "Float", "Zither filtered BAM alt frequency", lambda pileup_stats: pileup_stats.filtered_af)

DEFAULT_TAGS = [zither_total_depth, zither_total_af, filtered_depth, filtered_af]

        
def _build_execution_context(argv):
    return OrderedDict([("timestamp",
                         datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                        ("command",
                         ' '.join(argv)),
                        ("cwd",
                         os.getcwd()),
                        ("version",
                         __version__)])

def _get_sample_names(input_vcf):
    with open(input_vcf, 'r') as input_file:
        column_header = None
        sample_names = []
        for line in input_file.readlines():
            if not line.startswith("##"):
                if line.startswith("#"):
                    column_header = line.rstrip()
                    column_fields = column_header.split("\t")
                    n = len(column_fields)
                    sample_names = column_fields[9:(n+1)]
        return sample_names

def _build_reader_dict(sample_bam_mapping):
    readers_dict = OrderedDict()
    for (sample, bam_file) in sample_bam_mapping.items():
        readers_dict[sample] = _BamReader(bam_file)
    return readers_dict

def _build_column_header_line(sample_names):
    column_headers = list(_VCF_FIXED_HEADERS)
    column_headers.extend(sample_names)
    return '\t'.join(column_headers)

def _create_vcf(input_vcf, sample_reader_dict, execution_context, tags=DEFAULT_TAGS):
    exec_tags = ['{}="{}"'.format(k,v) for (k,v) in execution_context.items()]
    zither_metaheader = '##zither=<{}>'.format(",".join(exec_tags))

    vcf_headers = ['##fileformat=VCFv4.1']
    vcf_headers.extend([tag.metaheader for tag in tags])
    vcf_headers.append(zither_metaheader)
    
    FORMAT = ":".join([tag.id for tag in tags])
    
    with open(input_vcf, 'r') as input_file:

        print("\n".join(vcf_headers))
        print(_build_column_header_line(sample_reader_dict.keys()))
        for line in input_file.readlines():
            if not line.startswith("#"):
                vcf_fields = line.rstrip("\n").split("\t")[0:5]
                (CHROM, POS, dummy, REF, ALT) = vcf_fields
                vcf_fields.append('.')
                vcf_fields.append('.')
                vcf_fields.append('.')
                vcf_fields.append(FORMAT)
                for sample_name in sample_reader_dict.keys():
                    bam_reader = sample_reader_dict[sample_name]
                    pileup_stats = bam_reader.get_pileup_stats(CHROM,
                                                               int(POS),
                                                               REF,
                                                               ALT)
                    sample_field = [tag.get_value(pileup_stats) for tag in tags]
                    sample_field_joint = ':'.join(sample_field)
                    vcf_fields.append(sample_field_joint)
                a = '\t'.join(vcf_fields)
                print(a)

def _parse_command_line_args(arguments):
    parser = argparse.ArgumentParser(usage="zither [-h] [-V] input_vcf "
                                     "input_bam",
        description='''For all positions in VCF, pull raw depths and alt freqs "
        "from BAM file, writing output as new VCF to stdout. Type 'zither -h' "
        "for help''')

    parser.add_argument("-V",
                        "--version",
                        action='version',
                        version=__version__)
    parser.add_argument('input_vcf',
                        help="Path to input VCFs; all record locations will "
                        "appear in output file")
    parser.add_argument('--bam',
                        help="Path to indexed BAM; used to calculate raw depth "
                        "and frequency")
    parser.add_argument('--mapping_file',
                        help="Path to tab delimited list of VCF_sample_names "
                        "and BAM_file_names")
    parser.add_argument('--base-call-quality',
                        help="minimum base-call quality to be included. "
                        "Defaults to 0 (include all)")
    parser.add_argument('--mapq_minimum',
                        help="minimum mapping quality to be included. "
                        "Defaults to 0 (include all)")
    args = parser.parse_args(arguments)
    return args


def main(command_line_args):
    args = _parse_command_line_args(command_line_args[1:])
    execution_context = _build_execution_context(command_line_args)
    strategy = _get_sample_bam_strategy(args)
    sample_bam_mapping = strategy.build_sample_bam_mapping()
    reader_dict = _build_reader_dict(sample_bam_mapping)
    _create_vcf(args.input_vcf, reader_dict, execution_context)

if __name__ == '__main__':
    main(sys.argv)
