"""
Class to crop CT images to have only one subject per image.
It should work for pre-clinical and clinical images with different
resolutions.
"""
import os
import logging
import pickle
import numpy as np
import nibabel as nib
import nrrd
import cv2
from lung_segmentation.utils import split_filename
import matplotlib.pyplot as plot
from scipy.ndimage.interpolation import rotate


LOGGER = logging.getLogger('lungs_segmentation')
MOUSE_NAMES = ['mouse_01', 'mouse_02', 'mouse_03',
               'mouse_04', 'mouse_05', 'mouse_06']


class ImageCropping():

    def __init__(self, image, mask=None, prefix=None):
        LOGGER.info('Starting image cropping...')

        self.image = image
        self.mask = mask

        imagePath, imageFilename, imageExt = split_filename(image)
        self.extention = imageExt
        filename = imageFilename.split('.')[0]
        if mask is not None:
            _, maskFilename, maskExt = split_filename(mask)
            maskFilename = maskFilename.replace('.', '_')
            self.maskOutname = os.path.join(imagePath, maskFilename+'_cropped')+maskExt

        if prefix is None and mask is not None:
            self.imageOutname = os.path.join(imagePath, filename+'_cropped')+imageExt
        elif prefix is None and mask is None:
            self.imageOutname = os.path.join(imagePath, filename+'_cropped')
        elif prefix is not None and mask is None:
            self.imageOutname = os.path.join(imagePath, prefix+'_cropped')
        elif prefix is not None and mask is not None:
            self.imageOutname = os.path.join(imagePath, prefix+'_cropped')+imageExt

    def crop_with_mask(self):

        maskData, maskHD = nrrd.read(self.mask)
        if self.extention == '.nrrd':
            imageData, imageHD = nrrd.read(self.image)

            space_x = np.abs(imageHD['space directions'][0, 0])
            space_y = np.abs(imageHD['space directions'][1, 1])
            space_z = np.abs(imageHD['space directions'][2, 2])
        elif self.extention == '.nii.gz':
            imageData = nib.load(self.image).get_data()
            imageHD = nib.load(self.image).header

            space_x, space_y, space_z = imageHD.get_zooms()

        delta_x = int(10 / space_x)
        delta_y = int(10 / space_y)
        delta_z = int(10 / space_z)

        x, y, z = np.where(maskData==1)

        maskMax = np.max(maskData)
        maskMin = np.min(maskData)
        if maskMax > 1 and maskMin < 0:
            LOGGER.info('This image {} is probably not a mask, as it is not binary. '
                        'It will be ignored. Please check if it is true.'.format(self.mask))
            self.imageOutname = None
            self.maskOutname = None
        else:
            new_x = [np.min(x)-delta_x, np.max(x)+delta_x]
            new_x[0] = 0 if new_x[0] < 0 else new_x[0]
            new_x[1] = imageData.shape[0] if new_x[1] > imageData.shape[0] else new_x[1]

            new_y = [np.min(y)-delta_y, np.max(y)+delta_y]
            new_y[0] = 0 if new_y[0] < 0 else new_y[0]
            new_y[1] = imageData.shape[1] if new_y[1] > imageData.shape[1] else new_y[1]

            new_z = [np.min(z)-delta_z, np.max(z)+delta_z]
            new_z[0] = 0 if new_z[0] < 0 else new_z[0]
            new_z[1] = imageData.shape[2] if new_z[1] > imageData.shape[2] else new_z[1]

            croppedMask = maskData[new_x[0]:new_x[1], new_y[0]:new_y[1],
                                   new_z[0]:new_z[1]]

            croppedImage = imageData[new_x[0]:new_x[1], new_y[0]:new_y[1],
                                     new_z[0]:new_z[1]]
            if self.extention == '.nrrd':
                imageHD['sizes'] = np.array(croppedImage.shape)
                nrrd.write(self.imageOutname, croppedImage, header=imageHD)
            elif self.extention == '.nii.gz':
                im2save = nib.Nifti1Image(croppedImage, affine=nib.load(self.image).affine)
                nib.save(im2save, self.imageOutname)
            maskHD['sizes'] = np.array(croppedMask.shape)
            nrrd.write(self.maskOutname, croppedMask, header=maskHD)

        LOGGER.info('Cropping done!')
        return self.imageOutname, self.maskOutname

    def crop_wo_mask(self, accurate_naming=True):
        """
        Function to crop CT images automatically. It will look for edges
        in the middle slice and will crop the image accordingly.
        If accurate_naming is enabled, the numbering of the cropped
        images will account for missing subjects within the image.
        This will enable you to keep track of mice in longitudinal studies.
        This is for mouse experiment where more than one mouse is acquired
        in one image. If you are not cropping pre-clinical images or you
        are not interested in keep track of the mice across time-points,
        set this to False.
        """

        im, imageHD = nrrd.read(self.image)
        space_x = np.abs(imageHD['space directions'][0, 0])
        space_y = np.abs(imageHD['space directions'][1, 1])
        space_z = np.abs(imageHD['space directions'][2, 2])
        process = True
        indY = None
        out = []

        min_first_edge = int(63 / space_x)
        min_last_edge = im.shape[0] - int(63 / space_x)

        min_size_x = int(17 / space_x)
        if min_size_x > im.shape[0]:
            min_size_x = im.shape[0]
        min_size_y = int(30 / space_y)
        if min_size_y > im.shape[1]:
            min_size_y = im.shape[1]
            indY = im.shape[1]
        min_size_z = int(45 / space_z)
        if min_size_z > im.shape[2]:
            min_size_z = im.shape[2]

        _, _, dimZ = im.shape

        mean_Z = int(np.ceil((dimZ)/2))

#         average_intensity = (np.max(im)-np.min(im))/7
#         im[im<np.min(im)+average_intensity] = np.min(im)
#         im[im<np.min(im)+824] = np.min(im)

        n_mice_detected = []
        not_correct = True
        angle = 0
        counter = 0
        while not_correct:
            im[im<np.min(im)+824] = np.min(im)
            im[im == 0] = np.min(im)
            for offset in [20, 10, 0, -10, -15]:
                _, y1 = np.where(im[:, :, mean_Z+offset] != np.min(im))

                im[im==np.min(im)] = 0
                im[im!=0] = 1
                try:
                    im[:, np.min(y1)+min_size_y+10:, mean_Z+offset] = 0
                except:
                    print()
                nb_components, output, stats, _ = (
                    cv2.connectedComponentsWithStats(im[:, :, mean_Z+offset].astype(np.uint8),
                                                     connectivity=8))
                sizes = stats[1:, -1]
                nb_components = nb_components - 1
                min_size = 100/space_x
                img2 = np.zeros((output.shape))
                for i in range(0, nb_components):
                    if sizes[i] >= min_size:
                        img2[output == i + 1] = 1
                x, y = np.where(img2!=0)
                if x.any():
                    if indY is None and offset == 0:
                        indY = np.max(y)
                    uniq = sorted(list(set(x)))

                    xx = [uniq[0]]
                    for i in range(1, len(uniq)):
                        if uniq[i]!=uniq[i-1]+1:
                            xx.append(uniq[i-1])
                            xx.append(uniq[i])
                    xx.append(uniq[-1])
                    xx = sorted(list(set(xx)))
                    n_mice_detected.append(int(len(xx)//2))
                else:
                    n_mice_detected.append(0)
            if len(set(n_mice_detected)) == 1 or (len(set(n_mice_detected)) == 2 and 0 in set(n_mice_detected)):
                not_correct = False
            elif counter < 8:
                angle = angle - 2
                LOGGER.warning('Different number of mice have been detected going from down-up '
                               'in the image. This might be due to an oblique orientation '
                               'of the mouse trail. The CT image will be rotated about the z '
                               'direction of %f degrees', np.abs(angle))
                n_mice_detected = []
                indY = None
                im, _ = nrrd.read(self.image)
                im = rotate(im, angle, (0, 2), reshape=False, order=0)
                counter += 1
                if counter % 2 == 0:
                    mean_Z = mean_Z - 10
            else:
                LOGGER.warning('CT image has been rotated of 14° but the number of mice detected '
                               'is still not the same going from down to up. This CT cannot be '
                               'cropped properly and will be excluded.')
                proccess = False
                not_correct = False

        if process:
            im, _ = nrrd.read(self.image)
            if angle != 0:
                im = rotate(im, angle, (0, 2), reshape=False, order=0)
                im[im == 0] = np.min(im)

            if len(xx) % 2 != 0:
                LOGGER.warning('The number of detected edges is odd. This should not '
                               'happen and it could mean that the cropping is wrong. '
                               'The algorithm will remove the last edge and save the '
                               'images but there are high chances that this is wrong. '
                               'Please check the results.')
                xx.remove(xx[-1])
            if accurate_naming:
                image_names = MOUSE_NAMES.copy()
                first_edge = xx[0]
                last_edge = xx[-1]
                names2remove = []
                hole_found = 0
                missing_at_edge = False
                if first_edge > min_first_edge:
                    missing_left = int(np.round((first_edge-min_first_edge)/(min_size_x*2)))
                    if missing_left > 0:
                        LOGGER.info('There are {0} voxels between the left margin of the '
                                    'image and the first detected edge. This usually means that '
                                    'there are {1} missing mice on the left-end side. '
                                    'The mouse naming will be updated accordingly.'
                                    .format(first_edge-min_first_edge, missing_left))
                        for m in range(missing_left):
                            names2remove.append(image_names[m])
                        hole_found = hole_found+missing_left
                        missing_at_edge = True
                if last_edge < min_last_edge:
                    missing_right = int(np.round((min_last_edge-last_edge)/(min_size_x*2)))
                    if missing_right > 0:
                        LOGGER.info('There are {0} voxels between the right margin of the '
                                    'image and the last detected edge. This usually means that '
                                    'there are {1} missing mice on the right-end side. '
                                    'The mouse naming will be updated accordingly.'
                                    .format(min_last_edge-last_edge, missing_right))
                        for m in range(missing_right):
                            names2remove.append(image_names[-1-m])
                        hole_found = hole_found+missing_right
                        missing_at_edge = True
                for ind in names2remove:
                    image_names.remove(ind)
                if (last_edge - first_edge) < (6*min_size_x+5*(min_size_x/1.3)) and not missing_at_edge:
                    LOGGER.info('The distance between the first and the last detected edge is '
                                'not sufficient to accomodate 6 mice. This might mean that '
                                'there is one missing mouse on one side that was not detected '
                                'before. A further check will be performed in order to identify it.')
                    if (first_edge-min_first_edge) >= min_last_edge-last_edge:
                        LOGGER.info('Removing the first mouse.')
                        image_names.remove('mouse_01')
                    else:
                        LOGGER.info('Removing the last mouse.')
                        image_names.remove('mouse_06')
                    hole_found += 1
                mouse_distances = []

                for i, ind in enumerate(range(1, len(xx)-1, 2)):
                    mouse_index = image_names[i]
                    distance = xx[ind+1] - xx[ind]
                    mouse_distances.append(distance)
                    hole_dimension = int(np.round(distance/(min_size_x*1.5)))
                    if hole_dimension >= 2:
                        names2remove = []
                        LOGGER.info('The distance between mouse {0} and mouse {1} is '
                                    '{4} voxels, which is {2} times greater than the minimum '
                                    'mouse size. This could mean that {3} mice are missing'
                                    ' in this batch. They will'
                                    ' be ignored and the naming will be updated accordingly.'
                                    .format(mouse_index, image_names[i+1], hole_dimension,
                                            hole_dimension-1, distance))
                        for m in range(hole_dimension-1):
                            names2remove.append(image_names[i+m+1])
                        for ind in names2remove:
                            image_names.remove(ind)
                        hole_found += 1
                if hole_found + int(len(xx)/2) != 6:
                    names2remove = []
                    still_missing = 6 - (hole_found + int(len(xx)/2))
                    LOGGER.info('It seems that not all holes has been identified, since the '
                                'detected mice are {0} and the hole detected are {1}. '
                                'This means that there are still {2} mice missing in order to '
                                'reach the standard mice number (6). I will remove the names '
                                'belonging to the mouse with the biggest distance.'
                                .format(int(len(xx)/2), hole_found, still_missing))
                    for i in range(still_missing):
                        max_distance = np.where(np.asarray(mouse_distances)==
                                                np.max(np.asarray(mouse_distances)))[0][0]
                        names2remove.append(image_names[max_distance+1])
                        mouse_distances[max_distance] = 0
                    for ind in names2remove:
                            image_names.remove(ind)

            else:
                image_names = ['subject_0{}'.format(x+1) for x in range(int(len(xx)//2))]

            for n_mice, i in enumerate(range(0, len(xx), 2)):
                coordinates = {}
                mp = int((xx[i+1] + xx[i])/2)
                y0 = indY-int(min_size_y) if indY-int(min_size_y) > 0 else 0
                croppedImage = im[xx[i]:xx[i+1], y0:indY,
                                  mean_Z-int(min_size_z/2):mean_Z+int(min_size_z/2)]
                imageHD['sizes'] = np.array(croppedImage.shape)
                coordinates['x'] = [mp-int(min_size_x/2), mp+int(min_size_x/2)]
                coordinates['y'] = [y0, indY]
                coordinates['z'] = [mean_Z-int(min_size_z/2), mean_Z+int(min_size_z/2)]

                with open(self.imageOutname+'_{}.p'.format(image_names[n_mice]), 'wb') as fp:
                    pickle.dump(coordinates, fp, protocol=pickle.HIGHEST_PROTOCOL)

                nrrd.write(self.imageOutname+'_{}.nrrd'.format(image_names[n_mice]),
                           croppedImage, header=imageHD)
                out.append(self.imageOutname+'_{}.nrrd'.format(image_names[n_mice]))

        LOGGER.info('Cropping done!')
        return out
