# -*- coding: utf-8 -*-
"""k-means 聚类在本项目里的三个明确用途（不额外引依赖，纯 numpy 实现）。

1. **状态抽象**：把前端连续特征（速度比、拥堵指数、载重、坡度）压成有限个
   "交通-载荷工况"簇，给表格式 Q-learning 用。连续状态直接做表会爆炸，
   这是工程上唯一可行的降维手段。
2. **载重分箱**：``k_{i,j}`` 的下标 ``j``。载重是连续量，按 k-means 中心切段
   比人工定 5 t / 10 t 更有数据依据，且训练与推理共用一套边界。
3. **``k_{i,j}`` 标定**：对每个 (道路等级, 载重箱) 分组，取「实测/预测」比值的
   **中位数**作为修正系数——与 ``docs/45`` §5 的路段系数口径完全一致，
   中位数抗个别异常行程。

为什么自己写而不用 scikit-learn
-------------------------------
``requirements.txt`` 里没有 sklearn，为一个 120 行的算法加一个 30 MB 的重依赖
不划算（评委现场离线打包也会变重）。这里实现 k-means++ 初始化 + 多轮重启，
结果可复现（固定 seed）、可单测。若日后引入 sklearn，替换本文件即可，
对外接口（``fit`` / ``encode`` / ``edges``）不变。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


def _as_matrix(X: Any, *, name: str = "X") -> np.ndarray:
    matrix = np.asarray(X, dtype=float)
    if matrix.ndim == 1:
        matrix = matrix.reshape(-1, 1)
    if matrix.ndim != 2:
        raise ValueError("%s 必须是二维数组" % name)
    if not np.isfinite(matrix).all():
        raise ValueError("%s 含非有限值（NaN/Inf），先清洗再聚类" % name)
    return matrix


def _sq_distances(X: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    """``||x - c||²`` 矩阵，用 (x² - 2xc + c²) 展开，避免三维中间数组。"""
    x2 = np.sum(X * X, axis=1)[:, None]
    c2 = np.sum(centroids * centroids, axis=1)[None, :]
    return np.maximum(x2 - 2.0 * X @ centroids.T + c2, 0.0)


def _kmeans_once(X: np.ndarray, k: int, rng: np.random.Generator,
                 max_iter: int, tol: float) -> Tuple[np.ndarray, np.ndarray, float]:
    n = len(X)
    # k-means++：第一个中心随机，其余按 D² 概率抽样，显著降低退化簇概率
    centers = [X[int(rng.integers(0, n))]]
    closest = _sq_distances(X, np.asarray(centers)).ravel()
    for _ in range(1, k):
        total = float(closest.sum())
        if total <= 0:
            centers.append(X[int(rng.integers(0, n))])
        else:
            probs = closest / total
            centers.append(X[int(rng.choice(n, p=probs))])
        closest = np.minimum(closest, _sq_distances(X, np.asarray(centers))[:, -1])
    centroids = np.asarray(centers, dtype=float)

    labels = np.zeros(n, dtype=int)
    for _ in range(max(int(max_iter), 1)):
        distances = _sq_distances(X, centroids)
        labels = np.argmin(distances, axis=1)
        updated = centroids.copy()
        for index in range(k):
            members = X[labels == index]
            if len(members):
                updated[index] = members.mean(axis=0)
            else:  # 空簇：把最远点拉过来当新中心，避免 k 名不副实
                far = int(np.argmax(np.min(distances, axis=1)))
                updated[index] = X[far]
        shift = float(np.max(np.linalg.norm(updated - centroids, axis=1)))
        centroids = updated
        if shift <= tol:
            break
    inertia = float(np.sum(np.min(_sq_distances(X, centroids), axis=1)))
    return centroids, labels, inertia


def kmeans(X: Any, k: int, seed: int = 0, n_init: int = 8,
           max_iter: int = 200, tol: float = 1e-6) -> Dict[str, Any]:
    """k-means++ / 多启动取最优（惯量最小）。返回中心、标签、惯量。"""
    matrix = _as_matrix(X)
    n = len(matrix)
    if k < 1:
        raise ValueError("k 必须 >= 1")
    if k > n:
        raise ValueError("k=%d 大于样本数 %d，无法聚类" % (k, n))
    rng = np.random.default_rng(int(seed))
    best: Optional[Tuple[np.ndarray, np.ndarray, float]] = None
    for _ in range(max(int(n_init), 1)):
        centroids, labels, inertia = _kmeans_once(matrix, int(k), rng, max_iter, tol)
        if best is None or inertia < best[2] - 1e-12:
            best = (centroids, labels, inertia)
    assert best is not None
    centroids, labels, inertia = best
    return {
        "centroids": centroids,
        "labels": labels,
        "inertia": inertia,
        "k": int(k),
        "seed": int(seed),
        "silhouette": silhouette(matrix, labels),
    }


def silhouette(X: Any, labels: Any) -> float:
    """轮廓系数（-1..1）；簇数 = 1 或样本 < 4 时返回 0.0（无意义）。"""
    matrix = _as_matrix(X)
    labels = np.asarray(labels).astype(int)
    unique = np.unique(labels)
    if len(unique) < 2 or len(matrix) < 4:
        return 0.0
    distances = np.sqrt(np.maximum(
        np.sum(matrix * matrix, axis=1)[:, None]
        - 2.0 * matrix @ matrix.T
        + np.sum(matrix * matrix, axis=1)[None, :], 0.0))
    scores = []
    for i in range(len(matrix)):
        same = labels == labels[i]
        same[i] = False
        if not same.any():
            continue
        a = float(np.mean(distances[i, same]))
        b = min(float(np.mean(distances[i, labels == other]))
                for other in unique if other != labels[i])
        denom = max(a, b)
        scores.append((b - a) / denom if denom > 0 else 0.0)
    return float(np.mean(scores)) if scores else 0.0


class LoadBinClusterer:
    """一维载重分箱：k-means 中心 → 升序 → 相邻中心中点作为箱边界。"""

    def __init__(self, k: int = 4, seed: int = 0):
        self.k = max(int(k), 2)
        self.seed = int(seed)
        self.centers_: np.ndarray = np.zeros((0,))
        self.edges_: np.ndarray = np.zeros((0,))

    def fit(self, loads: Any) -> "LoadBinClusterer":
        values = np.asarray(loads, dtype=float).reshape(-1, 1)
        values = values[np.isfinite(values[:, 0])]
        if len(values) < self.k:
            raise ValueError("载重样本 %d 条，少于分箱数 %d" % (len(values), self.k))
        result = kmeans(values, self.k, seed=self.seed, n_init=4)
        centers = np.sort(result["centroids"].ravel())
        self.centers_ = centers
        self.edges_ = (centers[:-1] + centers[1:]) / 2.0
        return self

    @property
    def edges(self) -> List[float]:
        return [round(float(x), 4) for x in self.edges_]

    def assign(self, load_t: float) -> int:
        if len(self.edges_) == 0:
            raise RuntimeError("先 fit 再 assign")
        # side="right"：正好落在边界上的载重归入**上一箱**（边界是上一箱的上界）
        index = int(np.searchsorted(self.edges_, float(load_t), side="right"))
        return min(index, len(self.edges_))

    def assign_many(self, loads: Any) -> np.ndarray:
        values = np.asarray(loads, dtype=float).ravel()
        return np.array([self.assign(v) for v in values], dtype=int)


class StateQuantizer:
    """连续状态 → 离散簇：标准化 + k-means + 最近中心。

    ``features`` 是**有序**特征名，训练和推理必须同序；顺序落进 config，
    前端加字段时只能在末尾追加并 bump 版本（与 docs/07 数据字典同一纪律）。
    """

    def __init__(self, k: int = 10, seed: int = 20261103,
                 features: Optional[Sequence[str]] = None):
        self.k = max(int(k), 2)
        self.seed = int(seed)
        self.features: List[str] = list(features or [])
        self.centroids_: np.ndarray = np.zeros((0, 0))
        self.mean_: np.ndarray = np.zeros((0,))
        self.scale_: np.ndarray = np.zeros((0,))

    def fit(self, X: Any, features: Optional[Sequence[str]] = None) -> "StateQuantizer":
        matrix = _as_matrix(X)
        if features:
            if len(features) != matrix.shape[1]:
                raise ValueError("特征名数量与矩阵列数不一致")
            self.features = list(features)
        elif not self.features:
            self.features = ["f%d" % i for i in range(matrix.shape[1])]
        self.mean_ = matrix.mean(axis=0)
        scale = matrix.std(axis=0)
        self.scale_ = np.where(scale < 1e-9, 1.0, scale)
        result = kmeans(self._standardize(matrix), self.k, seed=self.seed, n_init=8)
        self.centroids_ = result["centroids"]
        self.inertia_ = float(result["inertia"])
        self.silhouette_ = float(result["silhouette"])
        return self

    def _standardize(self, matrix: np.ndarray) -> np.ndarray:
        return (matrix - self.mean_) / self.scale_

    def encode(self, x: Any) -> Tuple[int, float]:
        """返回 (簇号, 到中心的标准化距离)。未 fit 时抛错，不静默给 0。"""
        if self.centroids_.size == 0:
            raise RuntimeError("先 fit 再 encode")
        vector = np.asarray(x, dtype=float).reshape(1, -1)
        if vector.shape[1] != self.centroids_.shape[1]:
            raise ValueError("特征维度 %d 与训练时 %d 不一致"
                             % (vector.shape[1], self.centroids_.shape[1]))
        distances = _sq_distances(self._standardize(vector), self.centroids_)[0]
        label = int(np.argmin(distances))
        return label, float(math.sqrt(max(distances[label], 0.0)))

    def encode_many(self, X: Any) -> np.ndarray:
        matrix = _as_matrix(X)
        distances = _sq_distances(self._standardize(matrix), self.centroids_)
        return np.argmin(distances, axis=1).astype(int)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "k": self.k,
            "seed": self.seed,
            "features": self.features,
            "mean": [round(float(x), 6) for x in self.mean_],
            "scale": [round(float(x), 6) for x in self.scale_],
            "centroids": [[round(float(x), 6) for x in row] for row in self.centroids_],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "StateQuantizer":
        obj = cls(k=int(payload["k"]), seed=int(payload.get("seed", 0)),
                  features=payload.get("features"))
        obj.mean_ = np.asarray(payload["mean"], dtype=float)
        obj.scale_ = np.asarray(payload["scale"], dtype=float)
        obj.centroids_ = np.asarray(payload["centroids"], dtype=float)
        return obj


def calibrate_k_table(samples: Iterable[Dict[str, Any]],
                      load_bin_edges: Sequence[float],
                      min_group_samples: int = 3,
                      floor: float = 0.5, ceiling: float = 2.0) -> Dict[str, Any]:
    """按 (道路等级, 载重箱) 分组，用「实测/预测」比值中位数标定 ``k_{i,j}``。

    ``samples`` 每条形如 ``{"road_class": 2, "load_t": 18.0,
    "measured_kg": 1.42, "predicted_kg": 1.30}``。样本不足的组回退 1.0 并在
    ``fallback_groups`` 里点名——不拿 1 个样本编系数。
    """
    edges = np.asarray(list(load_bin_edges), dtype=float)
    groups: Dict[Tuple[int, int], List[float]] = {}
    for row in samples:
        road = int(row["road_class"])
        load = float(row["load_t"])
        predicted = float(row["predicted_kg"])
        measured = float(row["measured_kg"])
        if predicted <= 0 or measured < 0:
            continue
        index = min(int(np.searchsorted(edges, load, side="left")), len(edges))
        groups.setdefault((road, index), []).append(measured / predicted)
    table: Dict[str, Dict[str, float]] = {}
    fallback: List[Dict[str, Any]] = []
    for (road, index), ratios in sorted(groups.items()):
        row = table.setdefault(str(road), {})
        if len(ratios) >= int(min_group_samples):
            value = float(np.median(ratios))
            value = min(max(value, floor), ceiling)
            row[str(index)] = round(value, 4)
        else:
            row[str(index)] = 1.0
            fallback.append({"road_class": road, "load_bin": index,
                             "samples": len(ratios)})
    return {"table": table, "fallback_groups": fallback,
            "groups": len(groups), "samples": sum(len(v) for v in groups.values())}


__all__ = ["kmeans", "silhouette", "LoadBinClusterer", "StateQuantizer",
           "calibrate_k_table"]
