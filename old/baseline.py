import os
import pandas as pd
from statistics import mean, variance
from typing import List, Tuple
from polyglot.text import Text, Sentence, WordList
from utils.common import extract_entities, read_tokens, Entity
from functools import reduce
import nltk

nltk.download('stopwords')
STOP_WORDS = set(nltk.corpus.stopwords.words('slovene'))


def extract_text(inputFilePath: str) -> Text:
  lines: List[str]
  with open(inputFilePath) as f:
    lines = list(map(lambda l: l.replace('\n', ''), f.readlines()))
  for line in lines:
    if not line.startswith('#Text='): continue
    return Text(line[6:], hint_language_code='sl')


def extract_entity_context(entity: Entity, text: Text) -> List[Sentence]:
  def is_sentence_in_context(x: Sentence) -> bool:
    return len(list(filter(lambda ss: x.start <= ss[0] and ss[1] <= x.end, entity.substrings))) > 0

  return list(filter(is_sentence_in_context, text.sentences))


def normalize_sentiment(sentiment: int) -> int:
  if sentiment < 3: return -1
  if sentiment > 3: return 1
  return 0


def weight(d: int) -> float:
  if -3 <= d and d <= 3: return -1/10*abs(d)+2
  return 1


def mul(x: Tuple[float, float]) -> float:
  return x[0] * x[1]


def get_anchor_scores(e: Entity, s: Sentence, before = 8, after = 6) -> List[float]:
  scores = []
  rss = list(map(lambda ss: (ss[0]-s.start, ss[1]-s.start),filter(lambda ss: s.start <= ss[0] and ss[1] <= s.end, e.substrings)))

  processed = 0
  start = 0
  for i, w in enumerate(s.words):
    if processed == len(rss): break
    ss = rss[processed]
    if ss[0] == start and ss[1] == start + len(w):
      lower = min(before,i)
      upper = min(after,len(s.words)-i)
      weights = [weight(d-lower) for d in range(lower+upper+1)]
      polarities = list(map(lambda w: w.polarity, WordList(s.words[-lower+i:i+upper+1], language='sl')))
      score_plus = sum(map(mul, zip(weights, map(lambda p: 1 if p == 1 else 0, polarities))))
      score_minus = sum(map(mul, zip(weights, map(lambda p: -1 if p == -1 else 0, polarities))))
      flippers = 0 # todo - count negations
      scores.append(  (-1)**flippers * (score_plus-score_minus)/(score_plus+score_minus) if score_plus+score_minus > 0 else 0  )
      processed += 1
    start += len(w)+1

  return scores


def is_question(ss: List[Sentence]) -> bool:
  return len(list(filter(lambda s: '?' in s.raw, ss))) > 0


def is_exclaim(ss: List[Sentence]) -> bool:
  return len(list(filter(lambda s: '!' in s.raw, ss))) > 0


def num_polarity(e: Entity, s: Sentence, polarity_filter: int, before = 3, after = 3) -> int:
  count = 0
  rss = list(map(lambda ss: (ss[0]-s.start, ss[1]-s.start),filter(lambda ss: s.start <= ss[0] and ss[1] <= s.end, e.substrings)))
  
  # find entity anchor indexes relative to the sentence
  anchors = [False] * len(s.words)
  processed = 0
  start = 0
  for i, w in enumerate(s.words):
    if processed == len(rss): break
    ss = rss[processed]
    if ss[0] == start and ss[1] == start + len(w):
      anchors[i] = True
      processed += 1
    start += len(w)+1

  words = []
  # remove stop-words
  processed = 0
  for w in s.words:
    if not anchors[processed] and w.lower() in STOP_WORDS:
      del anchors[processed]
    else:
      words.append(w)
      processed += 1

  # count polarities in local context
  for i, a in enumerate(anchors):
    if not a: continue
    lower = min(before,i)
    upper = min(after,len(s.words)-i)
    count  += sum(map(lambda w: 1 if w.polarity == polarity_filter else 0, WordList(words[-lower+i:i+upper+1], language='sl')))

  return count


def get_sentiment_decision(anchor_scores: List[float], t_plus = 0.25, t_minus = -0.25, t_mixed = 0.5) -> int:
  avg = mean(anchor_scores) 
  var = variance(anchor_scores) if len(anchor_scores) > 1 else 0
  if avg < t_minus: return (-1, avg, var)
  if avg > t_plus: return (1, avg, var)
  if var > t_mixed: return (0, avg, var)
  return (0, avg, var)


def main():
  data = []
  for docId in sorted(map(lambda f: int(f[:-4]), os.listdir('./dataset'))):
    docPath = os.path.join('./dataset', f'{docId}.tsv')
    print(f'Extracting text... {{docId={docId}}}')
    text = extract_text(docPath)
    entities = extract_entities(read_tokens(docPath))
    for entity in entities:
      if not entity.sentiment or not entity.type: continue  # skip anomalies
      context_sentences = extract_entity_context(entity, text)

      # https://pdfs.semanticscholar.org/041e/0a842a9d039c14f03ff21dafa82cca202f50.pdf
      anchor_scores = reduce(lambda agg, s: agg + get_anchor_scores(entity, s), context_sentences, [])

      # https://arxiv.org/pdf/1506.03775.pdf
      is_person = (entity.type == 'PER')
      is_sub_obj = not is_person
      has_question = is_question(context_sentences)
      has_exclaim = is_exclaim(context_sentences)
      num_pos = sum(map(lambda s: num_polarity(entity, s, 1), context_sentences))
      num_neg = sum(map(lambda s: num_polarity(entity, s, -1), context_sentences))
      pos_vs_neg = (num_pos+1)/(num_neg+1)

      data.append((
        f'{docId}-{entity.id}',
        entity.getName(),
        entity.type,
        ' '.join(map(str, context_sentences)),
        is_person,
        is_sub_obj,
        has_question,
        has_exclaim,
        num_pos,
        num_neg,
        pos_vs_neg,
        len(anchor_scores),
        min(anchor_scores),
        max(anchor_scores),
        mean(anchor_scores),
        variance(anchor_scores) if len(anchor_scores) > 1 else 0,
        normalize_sentiment(entity.sentiment)
      ))

  df = pd.DataFrame(data, columns=['id', 'name', 'type', 'context', 'is_person', 'is_sub_obj', 'has_question', 'has_exclaim', 'num_pos', 'num_neg', 'pos_vs_neg', 'len', 'min', 'max', 'avg', 'var', 'sentiment'])
  df.to_csv(path_or_buf='cache/baseline.csv', index=False)

if __name__ == "__main__":
  main()