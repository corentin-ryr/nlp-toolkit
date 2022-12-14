# from torchtext.transforms import BERTTokenizer 
from typing import List
import torch

from nltk.tokenize import word_tokenize
from nltk import ngrams

from mixers.utils.hashing import MultiHashing
import numpy as np

from einops.layers.torch import Rearrange

# VOCAB_FILE = "https://huggingface.co/bert-base-uncased/resolve/main/vocab.txt"

# tokenizer = BERTTokenizer(vocab_path=VOCAB_FILE, do_lower_case=True, return_tokens=True)

# tokenizer("Hello World, How are you!") # single sentence input

# tokenizer(["Hello World","How are you!"]) # batch input


class collate_callable:
    def __init__(self, preprocessor=None) -> None:
        """Function used to collate data from a dataset when the samples are lists (the default collate function groups features together instead of grouping samples)

        Args:
            preprocessor (Callable, optional): Callable used on the data to preprocess it. If the label has the same type as the sample, the preprocessor yill be applied to the label as well. Defaults to None.
        """
        self.preprocessor = preprocessor

    def __call__(self, data):
        if len(data[0]) == 2:
            data, label = list(zip(*data))

            if type(label[0]) is torch.Tensor:
                label = torch.stack(label)
            elif type(label[0]) is type(data[0]): # Happens for autoencoding datasets for instance (the label is a copy of the data)
                if self.preprocessor:
                    label = self.preprocessor(label)

            if self.preprocessor:
                data = self.preprocessor(data)

            return data, label
        if len(data[0]) == 3:
            data0, data1, label = list(zip(*data))

            if type(label[0]) is torch.Tensor:
                label = torch.stack(label)
            elif type(label[0]) is type(data0[0]):
                if self.preprocessor:
                    label = self.preprocessor(label)

            if self.preprocessor:
                data0 = self.preprocessor(data0)
                data1 = self.preprocessor(data1)
            
            return data0, data1, label


def pad_list(l, length):
    l = l[:length]
    l += [""] * (length - len(l))
    return l


class ProjectiveLayer:
    def __init__(self, N: int, S: int, M: int, W: int) -> None:
        """_summary_
        Args:
            N (int): Number of hash functions
        """
        self.nbHashFunc = N
        self.sentenceLength = S
        self.bloomLength = M
        self.windowSize = W
        self.hashFunc = MultiHashing(self.nbHashFunc)

        self.rearrange = Rearrange("b n d -> b d n")

    def __call__(self, batchSentences: List[str]) -> torch.Tensor:

        sentencesMinHashes = np.zeros((len(batchSentences), self.sentenceLength, self.nbHashFunc), dtype=np.int64)

        for idxSentence, sentence in enumerate(batchSentences):
            if type(sentence) == str:
                sentence = word_tokenize(sentence)

            for idx, word in enumerate(pad_list(sentence, self.sentenceLength)):
                if type(word) == str:
                    wordGrammed = ["".join(i) for i in ngrams(word, 3)]
                    if not wordGrammed:
                        wordGrammed = [word]
                    word = wordGrammed

                sentencesMinHashes[idxSentence, idx] = np.min(
                    np.array([self.hashFunc.compute_hashes(gram) for gram in word]), axis=0
                )

        floatCounter = self.counting_bloom_filter(sentencesMinHashes)
        batchmovingWindowFloatCounter = self.moving_window(floatCounter)

        return self.rearrange(batchmovingWindowFloatCounter)

    def counting_bloom_filter(self, F: np.ndarray) -> torch.Tensor:
        """_summary_
        Args:
            F (list[list[int]]): input token hashes (F). Size: nb words x nb filters
        Returns:
            torch.Tensor: Float counting tensor. Size: bloom length x max sentence length
        """
        Fp = np.remainder(F, self.bloomLength)
        CountingBloom = torch.tensor(
            np.apply_along_axis(lambda x: np.bincount(x, minlength=self.bloomLength), axis=2, arr=Fp)
        )
        return torch.transpose(CountingBloom, 1, 2)

    def moving_window(self, floatCounter: torch.Tensor) -> torch.Tensor:
        l, m, s = floatCounter.shape
        movingFloatCounter = torch.zeros((l, (2 * self.windowSize + 1) * m, s))

        for idx, i in enumerate(range(self.windowSize, 0, -1)):
            movingFloatCounter[:, idx * m : (idx + 1) * m, i:] = floatCounter[:, :, : s - i]

        for i in range(self.windowSize + 1):
            movingFloatCounter[:, (i + self.windowSize) * m : (i + self.windowSize + 1) * m, : s - i] = floatCounter[
                :, :, i:
            ]

        return movingFloatCounter

