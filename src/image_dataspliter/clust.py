import os
import cv2
import numpy as np
from typing import List, Dict
from pycocotools.coco import COCO
import cv2
import numpy as np
from PIL import Image
from clusteval import clusteval
import pandas as pd
import json
from .feat import get_object_features, extract_object_features_per_image_wrapper
from .feat import extract_object_features_per_image, img_feature_extraction_implementor
from .feat import ImgPropertySetReturnType, run_multiprocess
import multiprocessing
from copy import deepcopy

def get_objects(imgname, coco, img_dir):
    try:
        val = next(obj for obj in coco.imgs.values() if obj["file_name"] == imgname)
    except StopIteration:
        raise ValueError(f"Image {imgname} not found in COCO dataset.")
    
    img_id = val['id']
    img_info = coco.loadImgs(img_id)[0]
    img_path = os.path.join(img_dir, imgname)
    image = cv2.imread(img_path)

    # Get annotation IDs for the image
    ann_ids = coco.getAnnIds(imgIds=img_id)
    anns = coco.loadAnns(ann_ids)
    img_obj = []

    for ann in anns:
        mask = coco.annToMask(ann)

        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            cropped_object = image[y:y+h, x:x+w]

            # Apply the mask to the cropped object
            mask_cropped = mask[y:y+h, x:x+w]
            print(f"mask_cropped: {mask_cropped.shape}")
            cropped_object = cv2.bitwise_and(cropped_object, cropped_object, mask=mask_cropped)
            
            # Remove the background (set to transparent)
            cropped_object = cv2.cvtColor(cropped_object, cv2.COLOR_BGR2RGBA)
            cropped_object[:, :, 3] = mask_cropped * 255

            img_obj.append(cropped_object)
    
    os.makedirs(name="crop_objs", exist_ok=True)
    for img_count, each_img_obj in enumerate(img_obj):
        cv2.imwrite(filename=f"crop_objs/img_obj_{img_count}.png", img=each_img_obj)
    
    return img_obj


def get_objects_keep_imgdim(imgname, coco, img_dir) -> List:
    try:
        val = next(obj for obj in coco.imgs.values() if obj["file_name"] == imgname)
    except StopIteration:
        raise ValueError(f"Image {imgname} not found in COCO dataset.")
    
    img_id = val['id']
    img_info = coco.loadImgs(img_id)[0]
    img_path = os.path.join(img_dir, imgname)
    image = cv2.imread(img_path)

    # Get annotation IDs for the image
    ann_ids = coco.getAnnIds(imgIds=img_id)
    anns = coco.loadAnns(ann_ids)
    img_obj = []

    for ann in anns:
        mask = coco.annToMask(ann)

        # Apply the mask to the image to get the segmented object
        segmented_object = cv2.bitwise_and(image, image, mask=mask)
        
        # Remove the background (set to transparent)
        segmented_object = cv2.cvtColor(segmented_object, cv2.COLOR_BGR2BGRA)
        segmented_object[:, :, 3] = mask * 255

        img_obj.append(segmented_object)
    
    os.makedirs(name="crop_objs", exist_ok=True)
    for img_count, each_img_obj in enumerate(img_obj):
        cv2.imwrite(filename=f"crop_objs/img_obj_{img_count}.png", img=each_img_obj)
    
    return img_obj


def get_objects_per_img(coco_annotation_file, img_dir, coco=None, img_names=None):
    if not coco:
        coco = COCO(annotation_file=coco_annotation_file)
    if not img_names:
        img_names = [obj["file_name"] for obj in coco.imgs.values()]
    if not isinstance(img_names, list):
        img_names = [img_names]
    img_objects = {}
    for imgname in img_names:
        img_objs = get_objects_keep_imgdim(imgname, coco, img_dir)
        if img_objs:
            img_objs = [cv2.cvtColor(obj, cv2.COLOR_RGBA2RGB) for obj in img_objs]
            img_objects[imgname] = img_objs        
    return img_objects

def get_objects_per_img_wrapper(args):
    img_objects = get_objects_per_img(**args)
    return img_objects
    
def get_obj_features_per_img(img_objects,img_resize_width,
                            img_resize_height,
                            model_family, model_name,
                            img_normalization_weight,
                            seed,
                            img_property_set: ImgPropertySetReturnType
                            ):
    img_feature = {}
    for imgname, objs in img_objects.items():
        feature = get_object_features(obj_imgs=objs, 
                                    img_resize_width=img_resize_width,
                                    img_resize_height=img_resize_height,
                                    model_family=model_family, model_name=model_name,
                                    img_normalization_weight=img_normalization_weight,
                                    seed=seed,
                                    )
        img_feature[imgname] = feature
    img_property_set.img_names = [img_name for img_name in img_feature.keys()]
    img_property_set.features = [feat for feat in img_feature.values()]
    return img_property_set

def get_obj_features_per_img_wrapper(args):
    img_property_set = get_obj_features_per_img(**args)
    return img_property_set

def cluster_img_features(img_property_set: ImgPropertySetReturnType) -> pd.DataFrame:
    img_names = img_property_set.img_names
    img_feats = img_property_set.features
    featarray = np.array(img_feats)
    ce = clusteval()
    results = ce.fit(featarray)
    clusters = results["labx"]
    imgcluster_dict = {"image_names": img_names, "clusters": clusters}
    imgclust_df = pd.DataFrame.from_dict(imgcluster_dict)
    return imgclust_df
        

def object_based_cluster_images_from_cocoann(coco_annotation_file, img_dir,
                                             img_property_set: ImgPropertySetReturnType,
                                             seed=2024, img_resize_width=224,
                                            img_resize_height=224,
                                            model_family="efficientnet",
                                            model_name="EfficientNetB0",
                                            img_normalization_weight="imagenet",
                                            
                                            ):
    
    img_objects = get_objects_per_img(coco_annotation_file=coco_annotation_file,
                                        img_dir=img_dir
                                        )
    img_property_set = get_obj_features_per_img(img_objects=img_objects, 
                                           img_resize_width=img_resize_width,
                                            img_resize_height=img_resize_height,
                                            model_family=model_family,
                                            model_name=model_name,
                                            img_normalization_weight=img_normalization_weight,
                                            seed=seed, 
                                            img_property_set=img_property_set
                                            )  
    cluster_df = cluster_img_features(img_property_set=img_property_set) 
    return cluster_df


def cluster_objects_with_added_features(img_dir, coco_annotation_filepath,
                                        img_property_set
                                        ):
    img_property_set= extract_object_features_per_image(coco_annotation_filepath, img_dir=img_dir,
                                                   img_property_set=img_property_set
                                                   )
    cluster_df = cluster_img_features(img_property_set=img_property_set) 
    return cluster_df

def cluster_with_full_image(img_property_set):
    img_property_set = img_feature_extraction_implementor(img_property_set)
    cluster_df = cluster_img_features(img_property_set=img_property_set) 
    return cluster_df

def clusters_with_full_image_multiprocess(img_property_set, **kwargs):
    img_property_set = run_multiprocess(img_property_set)
    cluster_df = cluster_img_features(img_property_set=img_property_set) 
    return cluster_df

def object_based_cluster_images_from_cocoann_multiprocess(coco_annotation_file, img_dir,
                                                          img_property_set,
                                                          seed=2024, img_resize_width=224,
                                                        img_resize_height=224,
                                                        model_family="efficientnet",
                                                        model_name="EfficientNetB0",
                                                        img_normalization_weight="imagenet"
                                                        ):
    coco = COCO(coco_annotation_file)
    img_names = [obj["file_name"] for obj in coco.imgs.values()]
    #get_objects_per_img(coco_annotation_file, img_dir, coco=None, img_names=None)
    args_objects_per_img = [{"coco_annotation_file": coco_annotation_file,
                            "img_dir": img_dir, 
                            "coco": coco,
                            "img_names": img_name
                            } for img_name in img_names
                            ]
    # chunksize = max(1, len(args_objects_per_img) // 10)
    # num_processes = multiprocessing.cpu_count()
    # from tqdm import tqdm
    # with multiprocessing.Pool(num_processes) as p:
    #     objects_results = list(
    #                 tqdm(
    #                     p.imap_unordered(
    #                         get_objects_per_img_wrapper, args_objects_per_img, 
    #                         chunksize=chunksize
    #                     ),
    #                     total=len(args_objects_per_img),
    #                 )
    #             )
    objects_results = parallelize_func(args=args_objects_per_img, func=get_objects_per_img_wrapper)
    print("multiprocess of get_objects_per_img completed")
    # img_objects = {}
    # for res in objects_results:
    #     for imgname, img_objs in res:
    #         img_objects[imgname] = img_objs
            
    # change below to multiprocess 
    args_get_obj_features_per_img = [{"img_objects": res, "img_resize_width": img_resize_width,
                                        "img_resize_height": img_resize_height, 
                                        "model_family": model_family,
                                        "model_name":model_name, 
                                        "img_normalization_weight": img_normalization_weight,
                                        "seed": seed, "img_property_set": img_property_set
                                        } for res in objects_results
                                    ]
    feat_results = parallelize_func(args=args_get_obj_features_per_img, 
                                    func=get_obj_features_per_img_wrapper
                                    )
    print("Completed multiprocesssing of get_obj_features_per_img")
    img_names, features = [], []
    for res in feat_results:
        img_names.extend(res.img_names)
        features.extend(res.features)
    img_property_set.img_names = img_names
    img_property_set.features = features
    
    print(f"Started clustering")
    cluster_df = cluster_img_features(img_property_set=img_property_set) 
    return cluster_df
    
def cluster_objects_with_added_features_multiprocess(img_dir, coco_annotation_filepath,
                                                    img_property_set
                                                    ):
    coco = COCO(coco_annotation_filepath)
    img_names = [obj["file_name"] for obj in coco.imgs.values()]
    #get_objects_per_img(coco_annotation_file, img_dir, coco=None, img_names=None)
    args_objects = [{"coco_annotation_filepath": coco_annotation_filepath,
                     "coco": deepcopy(coco),
                    "img_dir": img_dir,
                    "img_property_set": img_property_set,
                    "img_names": img_name
                    } for img_name in img_names
                    ]
    img_names, features = [], []
    img_property_set_results = parallelize_func(args=args_objects, 
                                                func=extract_object_features_per_image_wrapper
                                                )
    print(f"Completed multiprocessing of extract_object_features_per_image")
    for res in img_property_set_results:
        img_names.extend(res.img_names)
        features.extend(res.features)
    img_property_set.img_names = img_names
    img_property_set.features = features
    
    print("Started clustering")
    cluster_df = cluster_img_features(img_property_set=img_property_set) 
    return cluster_df
    
    
    
    

def parallelize_func(args, func):
    chunksize = max(1, len(args) // 10)
    num_processes = multiprocessing.cpu_count()
    from tqdm import tqdm
    with multiprocessing.Pool(num_processes) as p:
        results = list(
                    tqdm(
                        p.imap_unordered(
                            func, args, chunksize=chunksize
                        ),
                        total=len(args),
                    )
                )
    return results